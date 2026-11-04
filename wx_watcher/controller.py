from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .appstate import AppState, atomic_text
from .config import Config, default_sqlcipher
from .credentials import native_request
from .feishu import Feishu
from .reply import LocalAPI, ReplyConfig, ReplyStore, hash_id, now
from .services import Services
from .source import SnapshotReader, load_master, private_dir


class Controller:
    def __init__(self, directory=None):
        self.state = AppState(directory)
        self.services = Services(self.state)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wx-setup")
        self.jobs = {}
        self.lock = threading.RLock()
        self.preview_reader = None

    def submit(self, operation, data):
        id = str(uuid.uuid4())
        with self.lock:
            for old in list(self.jobs):
                if len(self.jobs) < 100:
                    break
                if self.jobs[old]["status"] != "running":
                    del self.jobs[old]
            self.jobs[id] = {
                "id": id,
                "operation": operation,
                "status": "running",
                "started": time.time(),
            }

        def run():
            try:
                value = self.perform(operation, data)
                self.jobs[id].update(status="completed", result=value)
            except Exception as exc:
                # Only our own bounded diagnostic messages reach the interface.
                message = (
                    str(exc)
                    if isinstance(
                        exc,
                        (ValueError, RuntimeError, FileNotFoundError, PermissionError),
                    )
                    else type(exc).__name__
                )
                self.jobs[id].update(status="failed", error=message[:600])
            self.jobs[id]["finished"] = time.time()

        self.pool.submit(run)
        return {"job_id": id}

    def doctor(self):
        checks = {
            "python": sys.version.split()[0],
            "sqlcipher": shutil.which(default_sqlcipher()),
            "lark_cli": shutil.which("lark-cli"),
            "platform": sys.platform,
        }
        try:
            profile = self.state.profile()
            with (Path(profile["db_root"]) / "contact/contact.db").open("rb") as stream:
                checks["wechat_data_access"] = {"readable": len(stream.read(16)) == 16}
        except PermissionError:
            checks["wechat_data_access"] = {
                "readable": False,
                "hint": "请在系统完整磁盘访问权限中允许实际运行的 WX Watcher 应用，然后退出并重新打开",
            }
        except (OSError, ValueError):
            checks["wechat_data_access"] = {
                "readable": False,
                "hint": "请先连接存在的微信数据库目录",
            }
        try:
            checks["keytool"] = native_request({"operation": "doctor"})
        except (OSError, RuntimeError) as exc:
            checks["keytool"] = {"ok": False, "error": str(exc)}
        try:
            checks["dsh"] = self.dsh().request("/v1/status")
        except (OSError, RuntimeError):
            checks["dsh"] = {"ready": False}
        return checks

    def dsh(self):
        return LocalAPI(
            self.state.load()["dsh_port"],
            Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
            / "storages/wx-reply-agent/token",
        )

    def feishu(self):
        s = self.state.load()
        return Feishu(s["feishu_transport"], s["feishu_app_id"], s["feishu_secret_ref"])

    def accounts(self):
        base = (
            Path.home()
            / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        )
        try:
            from .discovery import active_roots

            active = set(active_roots())
            return [
                {
                    "label": p.name,
                    "db_root": str(p / "db_storage"),
                    "active": (p / "db_storage").resolve() in active,
                }
                for p in sorted(base.iterdir())
                if p.is_dir() and (p / "db_storage/contact/contact.db").is_file()
            ]
        except PermissionError:
            raise PermissionError(
                "无法读取微信数据目录，请在系统隐私设置中允许本应用访问所需数据"
            ) from None
        except FileNotFoundError:
            return []

    def reader(self):
        p = self.state.profile()
        config = Config(
            db_root=Path(p["db_root"]),
            key_file=Path("/nonexistent"),
            state_dir=self.state.home / "preview" / p["id"],
            key_ref=p["key_ref"],
            sqlcipher=default_sqlcipher(),
        )
        if self.preview_reader is None or self.preview_reader.config != config:
            self.preview_reader = SnapshotReader(config)
        return self.preview_reader

    def conversations(self, query=""):
        reader = self.reader()
        rows = reader.rows(
            reader.config.db_root / "contact/contact.db",
            "SELECT username,nick_name,remark FROM contact WHERE username<>'' ORDER BY username LIMIT 10000;",
        )
        result = []
        for r in rows:
            if r["username"].endswith("@chatroom") or r["username"] == "filehelper":
                r["name"] = r.get("remark") or r.get("nick_name") or r["username"]
                if not query or query.lower() in (r["name"] + r["username"]).lower():
                    result.append(r)
        if not any(r["username"] == "filehelper" for r in result) and (
            not query or query in "文件传输助手"
        ):
            result.insert(0, {"username": "filehelper", "name": "文件传输助手"})
        return result[:200]

    def records(self):
        file = self.state.home / "config/reply.toml"
        if not file.exists():
            return []
        cfg = ReplyConfig.load(file)
        db = cfg.state_dir / "tasks.sqlite3"
        if not db.exists():
            return []
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as c:
            return [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT body FROM tasks ORDER BY updated_at DESC LIMIT 100"
                )
            ]

    def perform(self, operation, data):
        # Keep account/configuration changes atomic with service start and stop.
        if operation in {
            "settings",
            "import-key",
            "restore-key",
            "feishu-secret",
            "start",
            "stop",
            "task-review",
            "reset-cursor",
            "auto-connect",
            "finish-setup",
            "run-settings",
        }:
            with self.lock:
                return self._perform(operation, data)
        return self._perform(operation, data)

    def _perform(self, operation, data):
        if operation == "auto-connect":
            if self.services.running():
                raise ValueError("请先暂停监控，再检查或更新连接")
            from .onboarding import connect_existing

            return connect_existing(self)
        if operation == "finish-setup":
            self.perform("run-settings", data)
            verified = self.perform("verify-key", {})
            if not verified.get("all_verified"):
                raise RuntimeError("数据库凭据尚未全部验证通过，请先检查账号与数据权限")
            model = self.perform("test-dsh", {})
            if model.get("tools") != 0 or not (model.get("cost_meter") or {}).get(
                "found"
            ):
                raise RuntimeError("模型工具隔离或费用记账检查未通过，请检查 DSH 连接")
            delivery = self.perform("test-feishu", {})
            services = self.perform("start", {})
            return {
                "model_tools": model["tools"],
                "cost_recorded": True,
                "readback": delivery["readback"],
                "running": services["running"],
                "message": "模型、费用和飞书回读检查通过，消息助手已启动",
            }
        if operation == "run-settings":
            self.perform(
                "settings",
                {
                    k: data[k]
                    for k in ["send_enabled", "autostart_monitor"]
                    if k in data
                },
            )
            if "login_enabled" in data:
                if type(data["login_enabled"]) is not bool:
                    raise ValueError("登录启动开关无效")
                installed = (
                    Path.home()
                    / "Library/LaunchAgents/io.github.calwade.wx-watcher.plist"
                ).exists()
                if installed != data["login_enabled"]:
                    self.perform(
                        "login-service",
                        {"action": "install" if data["login_enabled"] else "uninstall"},
                    )
            return {"saved": True}
        if operation == "doctor":
            return self.doctor()
        if operation == "accounts":
            return self.accounts()
        if operation == "conversations":
            return self.conversations(str(data.get("query", ""))[:100])
        if operation == "import-key":
            if self.services.running():
                raise ValueError("请先暂停监听再连接其他账号")
            root = Path(str(data["db_root"])).expanduser().resolve()
            raw = load_master(Path(str(data["key_file"])).expanduser())
            answer = native_request(
                {"operation": "store", "db_root": str(root), "key_hex": raw.hex()}
            )
            self.state.add_profile(
                root,
                answer["key_ref"],
                str(data.get("label", "")),
                {k: v for k, v in answer.items() if k not in {"key_ref"}},
            )
            self.preview_reader = None
            return {
                "connected": True,
                "verified": answer["verified"],
                "total": answer["total"],
            }
        if operation == "verify-key":
            p = self.state.profile()
            return native_request(
                {
                    "operation": "verify",
                    "db_root": p["db_root"],
                    "key_ref": p["key_ref"],
                }
            )
        if operation == "backup-key":
            return native_request(
                {
                    "operation": "export_backup",
                    "key_ref": self.state.profile()["key_ref"],
                    "password": data["password"],
                }
            )
        if operation == "restore-key":
            if self.services.running():
                raise ValueError("请先暂停监听再恢复账号凭据")
            root = Path(str(data["db_root"])).expanduser().resolve()
            result = native_request(
                {
                    "operation": "import_backup",
                    "db_root": str(root),
                    "backup": data["backup"],
                    "password": data["password"],
                }
            )
            self.state.add_profile(
                root, result["key_ref"], str(data.get("label", "")), result
            )
            return {
                "connected": True,
                "verified": result["verified"],
                "total": result["total"],
            }
        if operation == "settings":
            if self.services.running():
                raise ValueError("请先暂停监听，再修改配置")
            allowed = {
                "active_profile",
                "chat_id",
                "group_name",
                "business_context",
                "knowledge_files",
                "reply_rules",
                "feishu_chat_id",
                "feishu_chat_name",
                "feishu_transport",
                "feishu_app_id",
                "dsh_port",
                "reader_port",
                "send_enabled",
                "autostart_monitor",
                "debounce_seconds",
                "max_daily_calls",
                "retention_days",
            }
            values = {k: v for k, v in data.items() if k in allowed}
            for k in [
                "business_context",
                "reply_rules",
                "group_name",
                "chat_id",
                "feishu_chat_id",
                "feishu_app_id",
            ]:
                if k in values and (
                    not isinstance(values[k], str) or len(values[k]) > 20000
                ):
                    raise ValueError("配置字段无效：" + k)
            for k in ["dsh_port", "reader_port"]:
                if k in values and (
                    type(values[k]) is not int or not 1024 <= values[k] <= 65535
                ):
                    raise ValueError("端口无效")
            if "active_profile" in values and values["active_profile"] not in {
                p["id"] for p in self.state.load()["profiles"]
            }:
                raise ValueError("账号配置不存在")
            if (
                "active_profile" in values
                and values["active_profile"] != self.state.load()["active_profile"]
            ):
                values.update(chat_id="", group_name="")
            for k in ["max_daily_calls", "retention_days"]:
                if k in values and (
                    type(values[k]) is not int or not 0 <= values[k] <= 10000
                ):
                    raise ValueError("数量设置无效")
            if "debounce_seconds" in values and (
                type(values["debounce_seconds"]) not in {float, int}
                or not 0 <= values["debounce_seconds"] <= 30
            ):
                raise ValueError("消息合并等待时间无效")
            if "chat_id" in values and not re.fullmatch(
                r"[A-Za-z0-9_.@-]{1,256}", values["chat_id"]
            ):
                raise ValueError("会话 ID 无效")
            if "knowledge_files" in values:
                from .knowledge import select_passages

                select_passages(
                    tuple(Path(p) for p in values["knowledge_files"]), "验证资料"
                )
            if "send_enabled" in values and type(values["send_enabled"]) is not bool:
                raise ValueError("发送开关无效")
            self.state.update(**values)
            return {"saved": True}
        if operation == "feishu-secret":
            if self.services.running():
                raise ValueError("请先暂停监听再修改飞书凭据")
            if not re.fullmatch(r"cli_[A-Za-z0-9]+", str(data.get("app_id", ""))):
                raise ValueError("飞书 App ID 无效")
            answer = native_request(
                {"operation": "store_secret", "secret": data["secret"]}
            )
            self.state.update(
                feishu_app_id=data["app_id"],
                feishu_secret_ref=answer["key_ref"],
                feishu_transport="openapi",
            )
            return {"saved": True}
        if operation == "feishu-chats":
            return self.feishu().chats()
        if operation == "test-feishu":
            s = self.state.load()
            if not s["feishu_chat_id"]:
                raise ValueError("请先选择飞书接收群")
            client = self.feishu()
            text = "【WX Watcher 连通性测试】机器人接入正常。本消息由你在本地应用中点击测试后发送。"
            result = client.send(
                s["feishu_chat_id"],
                text,
                "wxt-" + uuid.uuid4().hex,
            )
            message_id = result["data"]["message_id"]
            client.confirm_readback(message_id, text)
            return {"sent": True, "readback": True, "message_id": message_id}
        if operation == "test-dsh":
            api = self.dsh()
            id = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            job = api.request(
                "/v1/jobs",
                {
                    "id": id,
                    "prompt": json.dumps(
                        {
                            "conversation": "连接测试",
                            "trigger_message": {"text": "请问培训几点开始？"},
                            "business_facts": "尚未提供培训时间，请提示内部确认。",
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            end = time.monotonic() + 125
            while job["status"] in {"queued", "running"} and time.monotonic() < end:
                time.sleep(0.5)
                job = api.request("/v1/jobs/" + id)
            if job["status"] != "completed":
                raise RuntimeError("模型测试未完成，请查看 DSH 会话")
            from .reply import parse_proposal

            try:
                proposal = parse_proposal(job["output"])
            except (ValueError, TypeError, KeyError):
                raise RuntimeError(
                    "模型测试没有返回有效的回复建议格式，请检查 DSH 会话"
                ) from None
            return {
                "output": job["output"],
                "proposal": proposal,
                "usage": job.get("usage"),
                "cost_meter": job.get("cost_meter"),
                "tools": job.get("request_tool_count", job.get("visible_tools")),
                "session_id": job["session_id"],
            }
        if operation == "install-dsh":
            return self.install_dsh()
        if operation == "start":
            return self.services.start()
        if operation == "stop":
            return self.services.stop()
        if operation == "task-review":
            return self.review_task(data)
        if operation == "reset-cursor":
            if self.services.running() or data.get("confirmed") is not True:
                raise ValueError("请先暂停监听，并确认接受已过期事件无法补回的缺口")
            from .store import Store

            cfg = ReplyConfig.load(self.state.home / "config/reply.toml")
            reader = Store(Config.load(cfg.reader_config).state_dir)
            store = ReplyStore(cfg.state_dir)
            try:
                cursor = reader.event_cursor()
                with store.db:
                    resets = store.get_meta("cursor_resets", [])[-19:]
                    resets.append(
                        {"from": store.get_meta("cursor"), "to": cursor, "at": now()}
                    )
                    store.set_meta("cursor_resets", resets)
                    store.set_meta("cursor", cursor)
                    store.set_meta("last_error", None)
                return {
                    "saved": True,
                    "message": "已记录缺口并更新游标；重新开始监控后继续接收新消息",
                }
            finally:
                reader.close()
                store.db.close()
        if operation == "login-service":
            from .desktop import manage_login

            return manage_login(
                str(data.get("action", "status")), self.state.home, quiet=True
            )
        if operation == "capture-preflight":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).preflight(str(data.get("db_root", "")))
        if operation == "capture-start":
            if self.services.running():
                raise ValueError("请先暂停监听再开始首次取钥")
            from .keyflow import KeyFlow

            return KeyFlow(self.state).start(str(data["db_root"]))
        if operation == "capture-cancel":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).cancel(str(data["capture_id"]))
        if operation == "capture-login-ready":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).login_page_ready(str(data["capture_id"]))
        if operation == "capture-sessions":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).sessions()
        if operation == "capture-recover":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).recover(str(data["capture_id"]))
        if operation == "capture-status":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).status(str(data["capture_id"]))
        if operation == "diagnostics":
            s = self.state.load()
            return {
                "version": "0.2.0",
                "environment": self.doctor(),
                "services": self.services.status(),
                "profile_count": len(s["profiles"]),
                "destination_configured": bool(s["feishu_chat_id"]),
                "record_counts": {
                    k: sum(r["status"] == k for r in self.records())
                    for k in ["sent", "draft", "needs_review", "delivery_review"]
                },
            }
        raise ValueError("未知操作")

    def review_task(self, data):
        if self.services.running():
            raise ValueError("请先暂停监听，再处理任务；处理后可重新开始监控")
        config = ReplyConfig.load(self.state.home / "config/reply.toml")
        store = ReplyStore(config.state_dir)
        try:
            task = store.task(str(data.get("id", "")))
            if task is None or task["status"] not in {
                "draft",
                "needs_review",
                "delivery_review",
                "budget_review",
            }:
                raise ValueError("任务状态不支持此操作，请刷新后检查")
            action = data.get("decision")
            if action == "dismiss":
                task.update(status="dismissed", reviewed_at=now())
            elif (
                action == "mark-delivered"
                and task["status"] == "delivery_review"
                and data.get("confirmed") is True
            ):
                task.update(status="sent", reviewed_at=now(), manually_confirmed=True)
            elif action == "retry":
                if task["status"] == "delivery_review":
                    if data.get("confirmed_not_delivered") is not True:
                        raise ValueError("请先在飞书核对确实未投递，才能重新安排发送")
                    task.update(status="draft", reviewed_at=now(), error=None)
                else:
                    retry = {
                        **task,
                        "id": hash_id(task["id"] + str(uuid.uuid4())),
                        "retry_of": task["id"],
                        "status": "queued",
                        "created_at": now(),
                    }
                    for name in [
                        "dsh",
                        "proposal",
                        "error",
                        "delivery",
                        "feishu_text",
                        "drafted_at",
                    ]:
                        retry.pop(name, None)
                    store.save(retry)
                    task.update(
                        status="resolved", reviewed_at=now(), replacement_id=retry["id"]
                    )
            else:
                raise ValueError("任务处理选项无效")
            store.save(task)
            return {"saved": True, "message": "任务已处理；重新开始监控后执行待办"}
        finally:
            store.db.close()

    def install_dsh(self):
        with self.lock:
            return self._install_dsh()

    def _install_dsh(self):
        home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
        profile = home / "profiles/desktop"
        if not profile.is_dir():
            raise ValueError("未找到 DSH desktop 配置，请先安装并启动支持的 DSH 版本")
        source = Path(__file__).parent / "assets/dsh/index.mjs"
        if not source.exists():
            source = Path(__file__).resolve().parents[1] / "integrations/dsh/index.mjs"
        content = source.read_bytes()
        release = hashlib.sha256(content).hexdigest()[:16]
        try:
            current = self.dsh().request("/v1/status")
            if (
                current.get("ready")
                and current.get("source_digest", "")[:16] == release
            ):
                return {
                    "installed": True,
                    "build": release,
                    "ready": True,
                    "reused": True,
                }
        except (OSError, RuntimeError):
            pass
        target = self.state.home / "integrations/dsh" / release / "index.mjs"
        private_dir(target.parent)
        target.write_bytes(content)
        patch = profile / "cordis.patch.yml"
        old = patch.read_text() if patch.exists() else ""
        backup = self.state.home / "backups" / ("dsh-" + str(int(time.time())) + ".yml")
        private_dir(backup.parent)
        backup.write_text(old)
        backup.chmod(0o600)
        lines = old.splitlines(keepends=True)
        start = next(
            (
                i
                for i, line in enumerate(lines)
                if re.match(r"\s*- id: wx-reply-agent\s*$", line)
            ),
            None,
        )
        indent, end = 4, len(lines)
        if start is not None:
            indent = len(lines[start]) - len(lines[start].lstrip())
            end = start + 1
            while end < len(lines):
                line = lines[end]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                end += 1
            disabled = (
                lines[:start]
                + [
                    " " * indent + "- id: wx-reply-agent\n",
                    " " * (indent + 2) + "name: " + target.as_uri() + "\n",
                    " " * (indent + 2) + "disabled: true\n",
                ]
                + lines[end:]
            )
            atomic_text(patch, "".join(disabled))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(
                        ("127.0.0.1", self.state.load()["dsh_port"]), timeout=0.3
                    ):
                        pass
                except OSError:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(
                    "DSH 尚未卸载旧桥接。请在插件管理中停用本项目条目后重试；未重启其他会话。"
                )
        row = [
            " " * indent + "- id: wx-reply-agent\n",
            " " * (indent + 2) + "name: " + target.as_uri() + "\n",
            " " * (indent + 2) + "config:\n",
            " " * (indent + 4) + "port: " + str(self.state.load()["dsh_port"]) + "\n",
        ]
        if start is not None:
            # Preserve unrelated settings that may have changed while waiting for disposal.
            fresh = patch.read_text().splitlines(keepends=True)
            current = next(
                (
                    i
                    for i, line in enumerate(fresh)
                    if re.match(r"\s*- id: wx-reply-agent\s*$", line)
                ),
                None,
            )
            if current is None:
                raise RuntimeError("桥接配置被同时修改，请重试")
            finish = current + 1
            while finish < len(fresh):
                line = fresh[finish]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                finish += 1
            updated = "".join(fresh[:current] + row + fresh[finish:])
        else:
            updated = old + "\n- insert:\n" + "".join(row)
        atomic_text(patch, updated)
        endtime = time.monotonic() + 20
        while time.monotonic() < endtime:
            try:
                status = self.dsh().request("/v1/status")
                if status.get("build") == "0.2.0":
                    return {"installed": True, "build": release, "ready": True}
            except (OSError, RuntimeError):
                pass
            time.sleep(0.5)
        return {
            "installed": True,
            "ready": False,
            "hint": "请在 DSH 中检查插件状态，并确认 dsh-cost-meter 已安装",
        }
