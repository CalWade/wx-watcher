"""A complete local application using synthetic encrypted data and HTTP peers."""

import hashlib
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler

from test_pipeline import Fixture

from wx_watcher.api import create_server, load_token
from wx_watcher.appstate import AppState, atomic_json, atomic_text
from wx_watcher.collector import Collector
from wx_watcher.composition import build_desktop
from wx_watcher.config import Config
from wx_watcher.controller import Controller
from wx_watcher.desktop import create_desktop
from wx_watcher.domain.agents import AgentJob
from wx_watcher.feishu import Feishu
from wx_watcher.infrastructure.desktop_resources import DesktopResources
from wx_watcher.infrastructure.http_server import LoopbackHTTPServer
from wx_watcher.reply import ReplyAgent, ReplyConfig
from wx_watcher.source import SnapshotReader
from wx_watcher.store import Store

PROPOSAL = {
    "should_reply": True,
    "summary": "培训资料发送时间",
    "suggested_reply": "我会帮您确认培训资料的发送时间。",
    "reason": "资料中没有明确发送时间。",
    "needs_confirmation": ["培训资料发送时间"],
}
TOKEN = "sandbox-fixture-token"


class PeerState:
    def __init__(self, directory):
        self.file = directory / "peers.json"
        self.lock = threading.RLock()
        self.values = (
            json.loads(self.file.read_text())
            if self.file.exists()
            else {"jobs": {}, "messages": [], "submissions": 0}
        )
        self.mode = "normal"

    def save(self):
        atomic_json(self.file, self.values)


def start_peers(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return self.headers.get("Authorization") == "Bearer " + TOKEN

        def do_GET(self):
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            with state.lock:
                if self.path == "/v1/status":
                    return self.reply(
                        200,
                        {
                            "provider_id": "http",
                            "name": "自动验收 Agent",
                            "ready": True,
                            "capabilities": {
                                "cost_tracking": True,
                                "session_history": False,
                            },
                        },
                    )
                if self.path.startswith("/v1/jobs/"):
                    job = state.values["jobs"].get(self.path.rsplit("/", 1)[1])
                    return self.reply(
                        200 if job else 404,
                        {k: v for k, v in job.items() if k != "prompt_digest"}
                        if job
                        else {"error": "not_found"},
                    )
                if self.path.startswith("/open-apis/im/v1/chats"):
                    return self.reply(
                        200,
                        {
                            "code": 0,
                            "data": {
                                "items": [
                                    {"chat_id": "oc_sandbox", "name": "自动验收接收群"}
                                ],
                                "has_more": False,
                            },
                        },
                    )
                if self.path.startswith("/open-apis/im/v1/messages/"):
                    id = self.path.rsplit("/", 1)[1]
                    items = [
                        message
                        for message in state.values["messages"]
                        if message["message_id"] == id
                    ]
                    return self.reply(200, {"code": 0, "data": {"items": items}})
            return self.reply(404, {"error": "not_found"})

        def do_POST(self):
            if int(self.headers.get("Content-Length", "0")) > 200000:
                return self.reply(413, {"error": "too_large"})
            data = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            if self.path == "/open-apis/auth/v3/tenant_access_token/internal":
                if data != {"app_id": "cli_sandbox", "app_secret": "synthetic-secret"}:
                    return self.reply(403, {"code": 1})
                return self.reply(
                    200, {"code": 0, "tenant_access_token": TOKEN, "expire": 7200}
                )
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            with state.lock:
                if self.path == "/v1/prepare":
                    return self.reply(
                        200,
                        {
                            "provider_id": "http",
                            "name": "自动验收 Agent",
                            "ready": True,
                            "checks": [{"stage": "本地 Agent 接口", "ok": True}],
                        },
                    )
                if self.path == "/v1/jobs":
                    id, prompt = data["id"], data["prompt"]
                    old = state.values["jobs"].get(id)
                    if old is not None:
                        if (
                            old.get("prompt_digest")
                            != hashlib.sha256(prompt.encode()).hexdigest()
                        ):
                            return self.reply(409, {"error": "conflict"})
                        value = {
                            key: val
                            for key, val in old.items()
                            if key != "prompt_digest"
                        }
                        return self.reply(200, value)
                    state.values["submissions"] += 1
                    job = AgentJob(
                        id,
                        "http",
                        "failed" if state.mode == "failed" else "completed",
                        output="{}"
                        if state.mode == "invalid"
                        else json.dumps(PROPOSAL, ensure_ascii=False),
                        usage={"input_tokens": len(prompt) // 4, "output_tokens": 50},
                        cost={"recorded": True, "amount": 0.0, "currency": "USD"},
                        tool_count=0,
                        session_ref="synthetic-" + id,
                    ).as_dict()
                    state.values["jobs"][id] = {
                        **job,
                        "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
                    }
                    state.save()
                    return self.reply(200, job)
                if self.path.startswith("/open-apis/im/v1/messages?"):
                    old = next(
                        (
                            m
                            for m in state.values["messages"]
                            if m["uuid"] == data["uuid"]
                        ),
                        None,
                    )
                    if old is None:
                        old = {
                            "message_id": "sandbox-"
                            + str(len(state.values["messages"]) + 1),
                            "uuid": data["uuid"],
                            "chat_id": data["receive_id"],
                            "msg_type": "text",
                            "body": {"content": data["content"]},
                        }
                        state.values["messages"].append(old)
                        state.save()
                    return self.reply(
                        200, {"code": 0, "data": {"message_id": old["message_id"]}}
                    )
            return self.reply(404, {"error": "not_found"})

    server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class SandboxSettings(AppState):
    def __init__(self, directory, fixture):
        super().__init__(directory)
        self.fixture = fixture

    def write_configs(self):
        reader, reply = super().write_configs()
        text = reader.read_text()
        text += (
            "\nkey_file = "
            + json.dumps(str(self.fixture.config.key_file))
            + "\nbackend = "
            + json.dumps(self.fixture.config.backend)
            + "\n"
        )
        text = text.replace(
            "key_ref = " + json.dumps(self.profile()["key_ref"]), 'key_ref = ""'
        )
        atomic_text(reader, text)
        return reader, reply


class SandboxResources(DesktopResources):
    def __init__(self, state, fixture):
        super().__init__(state, None)
        self.fixture = fixture
        self.logged_in = True

    def accounts(self):
        return [
            {
                "db_root": str(self.fixture.root),
                "label": "自动验收账号",
                "active": self.logged_in,
            }
        ]

    def active_roots(self):
        return [self.fixture.root] if self.logged_in else []

    def reader(self):
        if self.preview_reader is None:
            self.preview_reader = SnapshotReader(
                replace(self.fixture.config, state_dir=self.state.home / "preview")
            )
        return self.preview_reader

    def refresh_account_names(self, root, quiet=False):
        return self.state.account_display(root)

    def doctor(self):
        return {"synthetic": True, "database_backend": self.fixture.config.backend}


class SandboxSupervisor:
    def __init__(self, state, delivery):
        self.state, self.delivery = state, delivery
        self.thread = None
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.collector = None

    def running(self):
        return bool(
            self.thread
            and self.thread.is_alive()
            and self.ready.is_set()
            and not self.stop_event.is_set()
        )

    def status(self):
        return {
            "running": self.running(),
            "last_error": self.error,
            "reader": self.collector.status()
            if self.collector
            else {"state": "stopped"},
        }

    def start(self):
        if self.running():
            return self.status()
        self.stop_event.clear()
        self.ready.clear()
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        if not self.ready.wait(20):
            self.stop()
            raise RuntimeError(self.error or "验收环境启动超时")
        if self.error:
            raise RuntimeError(self.error)
        return self.status()

    def run(self):
        server, reader_store, agent = None, None, None
        try:
            reader_file, reply_file = self.state.write_configs()
            config = Config.load(reader_file)
            reader_store = Store(config.state_dir)
            self.collector = Collector(config, reader_store)
            self.collector.poll()
            server = create_server(
                reader_store, self.collector, load_token(config.state_dir), 0
            )
            self.state.update(reader_port=server.server_port)
            reader_file, reply_file = self.state.write_configs()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            agent = ReplyAgent(ReplyConfig.load(reply_file), delivery=self.delivery)
            agent.initialize()
            self.collector.start()
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    agent.tick()
                    self.error = None
                except (OSError, RuntimeError, ValueError) as error:
                    self.error = str(error)[:200]
                self.stop_event.wait(0.2)
        except Exception as error:
            self.error = type(error).__name__ + ": sandbox startup failed"
            self.ready.set()
        finally:
            if self.collector:
                self.collector.stop()
            if agent:
                agent.store.close()
            if server:
                server.shutdown()
                server.server_close()
            if reader_store:
                reader_store.close()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(15)
            if self.thread.is_alive():
                raise RuntimeError("验收服务仍在关闭中")
        return self.status()


class Sandbox:
    def __init__(self, root, encrypted=True):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.writer_lock = threading.RLock()
        self.fixture = Fixture(self.root / "source", encrypted=encrypted)
        self.peers = PeerState(self.root)
        self.peer_server, self.peer_thread = start_peers(self.peers)
        token_file = self.root / "agent-token"
        atomic_text(token_file, TOKEN)
        self.state = SandboxSettings(self.root / "app", self.fixture)
        self.state.add_profile(
            self.fixture.root,
            "sandbox-credential",
            "自动验收账号",
            {"verified": 2, "total": 2},
        )
        self.state.update(
            agent_provider="http",
            agent_options={
                "port": self.peer_server.server_port,
                "token_file": str(token_file),
            },
            feishu_transport="openapi",
            feishu_app_id="cli_sandbox",
            feishu_secret_ref="sandbox-secret",
        )
        delivery = Feishu(
            "openapi",
            "cli_sandbox",
            "sandbox-secret",
            api_root=f"http://127.0.0.1:{self.peer_server.server_port}/open-apis/",
            secret_reader=lambda reference: "synthetic-secret",
        )
        deps = build_desktop(self.state.home, lambda: None, self.credentials)
        deps.services.shutdown_event.set()
        deps.state = self.state
        deps.resources = SandboxResources(self.state, self.fixture)
        deps.services = SandboxSupervisor(self.state, delivery)
        from wx_watcher.infrastructure.task_access import SqliteTaskAccess

        deps.tasks = SqliteTaskAccess(self.state)
        deps.delivery_client = lambda: delivery
        deps.agent_factory = lambda: deps.agent_registry.create(
            "http", self.state.load()["agent_options"]
        )
        self.controller = Controller(dependencies=deps)
        self.next_id = 2

    def credentials(self, request):
        if request["operation"] != "verify" or request.get("db_root") != str(
            self.fixture.root
        ):
            raise ValueError("验收环境仅验证自身的合成凭据")
        self.controller.reader().rows(
            self.fixture.root / "contact/contact.db",
            "SELECT username FROM contact LIMIT 1",
        )
        return {"all_verified": True, "verified": 2, "total": 2}

    def emit(self, text):
        if not isinstance(text, str) or not text or len(text) > 2000:
            raise ValueError("合成测试消息必须为 1 到 2000 字")
        with self.writer_lock:
            self.fixture.insert(self.fixture.shard, self.next_id, text)
            self.next_id += 1
        return {"accepted": True}

    def snapshot(self):
        with self.peers.lock:
            return {
                "synthetic": True,
                "backend": self.fixture.config.backend,
                "submissions": self.peers.values["submissions"],
                "messages": list(self.peers.values["messages"]),
                "services": self.controller.services.status(),
                "tasks": self.controller.records(),
            }

    def server(self, port=0):
        server = create_desktop(self.controller, TOKEN, port)
        base = server.RequestHandlerClass
        sandbox = self

        class Handler(base):
            def do_GET(self):
                if self.path == "/_sandbox/status" and self.authorized():
                    return self.send(200, sandbox.snapshot())
                return super().do_GET()

            def do_POST(self):
                if not self.path.startswith("/_sandbox/"):
                    return super().do_POST()
                origin = f"http://127.0.0.1:{self.server.server_port}"
                if (
                    not self.authorized()
                    or self.headers.get("Origin") != origin
                    or self.headers.get("X-NoteBridge-UI") != "1"
                ):
                    return self.send(403, {"error": "forbidden"})
                size = int(self.headers.get("Content-Length", "0"))
                if size > 10000:
                    return self.send(413, {"error": "too_large"})
                data = json.loads(self.rfile.read(size) or b"{}")
                if self.path == "/_sandbox/shutdown":
                    self.send(200, {"stopping": True})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
                if self.path == "/_sandbox/emit":
                    return self.send(200, sandbox.emit(data.get("text")))
                if self.path == "/_sandbox/agent-mode" and data.get("mode") in {
                    "normal",
                    "failed",
                    "invalid",
                }:
                    sandbox.peers.mode = data["mode"]
                    return self.send(200, {"saved": True})
                if self.path == "/_sandbox/login":
                    sandbox.controller.dependencies.resources.logged_in = bool(
                        data.get("logged_in")
                    )
                    return self.send(200, {"saved": True})
                return self.send(404, {"error": "unknown_operation"})

        server.RequestHandlerClass = Handler
        return server

    def close(self):
        self.controller.services.stop()
        self.controller.pool.shutdown(wait=True)
        self.peer_server.shutdown()
        self.peer_server.server_close()
        self.peer_thread.join()
        self.fixture.close()
