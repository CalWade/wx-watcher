"""In-memory implementations of application ports."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from wx_watcher.domain.account_names import clean_label, display_name
from wx_watcher.domain.agents import AgentJob

PROPOSAL = '{"should_reply":true,"summary":"培训资料","suggested_reply":"我帮您确认资料发送时间。","reason":"发送时间待确认","needs_confirmation":["资料发送时间"]}'


class MemorySettings:
    home = Path("/synthetic/app")

    def __init__(self):
        self.values = {
            "profiles": [
                {
                    "id": "owner",
                    "db_root": "/synthetic/account/db_storage",
                    "key_ref": "synthetic-ref",
                    "label": "合成账号",
                }
            ],
            "active_profile": "owner",
            "chat_id": "",
            "group_name": "",
            "send_enabled": False,
            "autostart_monitor": False,
            "feishu_transport": "cli",
            "feishu_app_id": "",
            "feishu_chat_id": "oc_synthetic",
            "feishu_chat_name": "合成接收群",
            "agent_provider": "embedded-test",
            "agent_options": {},
            "account_names": {},
        }

    def load(self):
        return deepcopy(self.values)

    def update(self, **values):
        self.values.update(deepcopy(values))
        return self.load()

    def profile(self):
        return next(
            p
            for p in self.load()["profiles"]
            if p["id"] == self.values["active_profile"]
        )

    def add_profile(self, root, reference, label="", verification=None):
        profile = {
            "id": str(root),
            "db_root": str(root),
            "key_ref": reference,
            "label": label,
            "verification": verification or {},
        }
        self.values["profiles"] = [
            p for p in self.values["profiles"] if p["db_root"] != str(root)
        ] + [profile]
        self.values["active_profile"] = profile["id"]
        return profile

    def account_display(self, root, settings=None):
        state = settings or self.values
        profile = next(
            (p for p in state["profiles"] if p["db_root"] == str(root)), None
        )
        return display_name(
            root, state.get("account_names", {}).get(str(root)), profile
        )

    def save_account_names(self, root, **values):
        if "custom_label" in values:
            values["custom_label"] = clean_label(values["custom_label"])
        self.values["account_names"].setdefault(str(root), {}).update(values)
        return self.account_display(root)


class ManualClock:
    def __init__(self):
        self.seconds = 1700000000.0

    def timestamp(self):
        return "2026-01-01T00:00:00+00:00"

    def time(self):
        return self.seconds

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


class MemoryTasks:
    def __init__(self):
        self.meta = {}
        self.records = {}
        self.waiting = {}

    @contextmanager
    def open(self):
        yield self

    def recent(self):
        return self.tasks()

    def has_inflight(self):
        return bool(self.tasks(["queued", "inference", "sending"]) or self.waiting)

    def reset_cursor(self):
        self.meta["cursor"] = 100
        return {"saved": True}

    def get_meta(self, key, default=None):
        return deepcopy(self.meta.get(key, default))

    def set_meta(self, key, value):
        self.meta[key] = deepcopy(value)

    def update_metadata(self, values):
        self.meta.update(deepcopy(values))

    def bind_scope(self, scope):
        if self.meta.get("scope", scope) != scope:
            raise ValueError("scope changed")
        self.meta["scope"] = scope

    def save(self, task):
        self.records[task["id"]] = deepcopy(task)

    def save_many(self, tasks):
        replacement = deepcopy(self.records)
        for task in tasks:
            replacement[task["id"]] = deepcopy(task)
        self.records = replacement

    def task(self, id):
        return deepcopy(self.records.get(id))

    def tasks(self, statuses=None, limit=100):
        return deepcopy(
            [
                task
                for task in self.records.values()
                if not statuses or task["status"] in statuses
            ][:limit]
        )

    def accept(self, task, cursor):
        self.records.setdefault(task["id"], deepcopy(task))
        self.meta["cursor"] = cursor

    def enqueue_pending(self, task, received):
        self.waiting.setdefault(
            task["id"], {"id": task["id"], "task": deepcopy(task), "received": received}
        )
        self.meta["cursor"] = task["event_id"]

    def pending(self, limit=10):
        return deepcopy(list(self.waiting.values())[:limit])

    def commit_batch(self, task, ids):
        self.save(task)
        for id in ids:
            self.waiting.pop(id)

    def record_inference(self, task, day, count_call):
        self.save(task)
        if count_call:
            calls = self.meta.setdefault("daily_calls", {})
            calls[day] = calls.get(day, 0) + 1

    def complete_tick(self, timestamp, wall_time, retention_days):
        self.update_metadata({"last_success": timestamp, "last_error": None})

    def counts(self):
        result = {}
        for task in self.records.values():
            result[task["status"]] = result.get(task["status"], 0) + 1
        return result

    def close(self):
        pass


class EmbeddedGateway:
    """A second provider proves the core needs only AgentGateway."""

    provider_id = "embedded-test"

    def __init__(self):
        self.jobs = {}
        self.calls = []
        self.tool_count = 0
        self.cost_recorded = True
        self.cost_tracking = True
        self.job_status = "completed"
        self.output = PROPOSAL
        self.ready = True

    def prepare(self):
        self.calls.append(("prepare",))
        return self.status()

    def status(self):
        return {
            "provider_id": self.provider_id,
            "name": "Embedded test agent",
            "ready": self.ready,
            "capabilities": {"cost_tracking": self.cost_tracking},
        }

    def submit(self, id, prompt):
        self.calls.append(("submit", id, prompt))
        self.jobs.setdefault(id, prompt)
        return self.poll(id)

    def poll(self, id):
        self.calls.append(("poll", id))
        return AgentJob(
            id,
            self.provider_id,
            self.job_status,
            output=self.output,
            usage={"input_tokens": 20, "output_tokens": 10},
            cost={"recorded": self.cost_recorded, "amount": 0.001},
            tool_count=self.tool_count,
        )


class MemoryDelivery:
    def __init__(self):
        self.messages = []

    def chats(self):
        return {"data": {"chats": [{"chat_id": "oc_synthetic", "name": "合成接收群"}]}}

    def send(self, chat, text, key, dry_run=False):
        if not dry_run:
            self.messages.append((chat, text, key))
        return {"data": {"message_id": "synthetic-message"}}

    def confirm_readback(self, id, text):
        if id != "synthetic-message" or self.messages[-1][1] != text:
            raise RuntimeError("readback mismatch")
        return True


class MemorySupervisor:
    def __init__(self):
        self.active = False
        self.calls = []

    def running(self):
        return self.active

    def start(self):
        self.calls.append("start")
        self.active = True
        return self.status()

    def stop(self):
        self.calls.append("stop")
        self.active = False
        return self.status()

    def status(self):
        return {"running": self.active}


def reply_policy(**values):
    options = dict(
        context_limit=20,
        knowledge_files=(),
        business_context="",
        reply_rules="",
        debounce_seconds=0,
        max_daily_calls=0,
        send_enabled=False,
        feishu_chat_id="oc_synthetic",
        feishu_chat_name="合成接收群",
        retention_days=0,
    )
    options.update(values)
    return SimpleNamespace(**options)


class MemoryReader:
    def __init__(self, root=Path("/synthetic/account/db_storage")):
        from wx_watcher.domain.replies import hash_id

        self.events = []
        self.calls = []
        self.status_value = {
            "state": "ready",
            "scope_id": hash_id(str(root) + "\n123@chatroom"),
            "chat_id": "123@chatroom",
            "group_name": "合成客户群",
            "event_cursor": 0,
        }

    def request(self, route, data=None):
        self.calls.append(route)
        if route == "/v1/status":
            return dict(self.status_value)
        if route.startswith("/v1/messages"):
            return {"messages": deepcopy(self.events)}
        if route.startswith("/v1/events"):
            from urllib.parse import parse_qs, urlsplit

            after = int(parse_qs(urlsplit(route).query)["after"][0])
            events = [event for event in self.events if event["event_id"] > after]
            return {
                "events": deepcopy(events),
                "next_cursor": events[-1]["event_id"] if events else after,
            }
        raise AssertionError("Unexpected reader route")

    def append(self, text, **values):
        event = {
            "event_id": len(self.events) + 1,
            "message_id": str(len(self.events) + 1),
            "content": text,
            "created_at": 1,
            "local_id": len(self.events) + 1,
            "type": 1,
        }
        event.update(values)
        self.events.append(event)
        self.status_value["event_cursor"] = event["event_id"]


class MemoryCapture:
    def __init__(self):
        self.calls = []
        self.phase = "ready_for_login"

    def preflight(self, root="", discover_account=False):
        self.calls.append(("preflight", root, discover_account))
        return {"ready": True}

    def start(self, root="", discover_account=False):
        self.calls.append(("start", root, discover_account))
        return {"capture_id": "synthetic-capture", "phase": self.phase}

    def status(self, id):
        return {"phase": self.phase, "db_root": "/synthetic/account/db_storage"}

    def cancel(self, id):
        self.phase = "cancelled"
        return {"cancel_requested": True}

    def login_page_ready(self, id):
        return {"ready": True}

    def recover(self, id):
        self.phase = "recovered"
        return {"phase": self.phase}

    def sessions(self):
        return [self.status("synthetic-capture")]


class MemoryContext:
    def __init__(self):
        self.state = MemorySettings()
        self.services = MemorySupervisor()
        self.tasks = MemoryTasks()
        self.capture = MemoryCapture()
        self.clock = ManualClock()
        self.gateway = EmbeddedGateway()
        self.delivery = MemoryDelivery()
        self.preview_reader = None
        self.credential_calls = []
        self.action_calls = []
        self.metadata_calls = []
        self.login_enabled = False
        self.discovered = [
            {
                "db_root": self.state.profile()["db_root"],
                "label": "合成账号",
                "active": True,
            }
        ]
        self.active = [Path(self.state.profile()["db_root"])]
        from wx_watcher.application.accounts import AccountsUseCases

        self.accounts_app = AccountsUseCases(self)

    def accounts(self):
        if isinstance(self.discovered, Exception):
            raise self.discovered
        return deepcopy(self.discovered)

    def active_roots(self):
        return list(self.active)

    def canonical_root(self, value):
        return Path(value)

    def account_status(self):
        return self.accounts_app.account_status()

    def require_current_account(self, profile=None):
        return self.accounts_app.require_current_account(profile)

    def require_local_account(self):
        return self.accounts_app.require_local_account()

    def conversations(self, query=""):
        return self.accounts_app.conversations(query)

    def read_conversations(self, query=""):
        return [{"username": "filehelper", "name": "文件传输助手"}]

    def refresh_account_names(self, root, quiet=False):
        self.metadata_calls.append((root, quiet))
        return self.state.save_account_names(root, nickname="合成昵称")

    def credential_request(self, data):
        self.credential_calls.append(data)
        return {
            "key_ref": "synthetic-new-reference",
            "verified": 2,
            "total": 2,
            "all_verified": True,
        }

    def load_key_file(self, value):
        return bytes(range(32))

    def close_preview(self):
        self.preview_reader = None

    def select_passages(self, files, query):
        return "", []

    def agent(self):
        return self.gateway

    def validate_agent(self, provider_id, options):
        if provider_id not in {"embedded-test", "other-test"}:
            raise ValueError("unknown provider")

    def feishu(self):
        return self.delivery

    def login_installed(self):
        return self.login_enabled

    def manage_login(self, action):
        self.login_enabled = action == "install"
        return {"installed": self.login_enabled}

    def doctor(self):
        return {"synthetic": True}

    def assert_data_access(self):
        return None

    def records(self):
        return self.tasks.tasks()

    def connect_existing(self):
        return {"agent": self.gateway.prepare()}

    def check_first_connection(self, root, discover):
        return {"ready": True, "db_root": root, "discover_account": discover}

    def perform(self, operation, data):
        from wx_watcher.application.connections import ConnectionsUseCases
        from wx_watcher.application.credentials import CredentialsUseCases
        from wx_watcher.application.runtime import RuntimeUseCases
        from wx_watcher.application.settings import SettingsUseCases
        from wx_watcher.application.tasks import TasksUseCases

        self.action_calls.append(operation)
        groups = [
            self.accounts_app,
            CredentialsUseCases(self),
            SettingsUseCases(self),
            ConnectionsUseCases(self),
            RuntimeUseCases(self),
            TasksUseCases(self),
        ]
        for group in groups:
            if operation in group.operations:
                return group.handle(operation, data)
        raise ValueError("unknown operation")
