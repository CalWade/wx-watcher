from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .appstate import AppState, atomic_text
from .branding import BRIDGE_ID, LEGACY_BRIDGE_ID, VERSION, dsh_storage, login_file
from .config import Config, default_sqlcipher
from .credentials import native_request
from .feishu import Feishu
from .reply import LocalAPI, ReplyConfig, ReplyStore, hash_id, now
from .services import Services
from .source import SnapshotReader, load_master, private_dir


class Controller:
    def __init__(self, directory=None):
        self.state = AppState(directory)
        self.services = Services(self.state, account_guard=self.require_current_account)
        self.pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="notebridge-setup"
        )
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
                "hint": "请在系统完整磁盘访问权限中允许实际运行的 NoteBridge 应用，然后退出并重新打开",
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
            dsh_storage() / "token",
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
                    **self.state.account_display(p / "db_storage"),
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

    def account_status(self):
        saved = self.state.load()
        unavailable = False
        try:
            discovered = self.accounts()
        except PermissionError:
            discovered, unavailable = [], True
        profiles = {str(Path(p["db_root"]).resolve()): p for p in saved["profiles"]}
        rows = {}
        for account in discovered:
            root = str(Path(account["db_root"]).resolve())
            rows[root] = {**account, "db_root": root, "available": True}
        for root, profile in profiles.items():
            rows.setdefault(
                root,
                {
                    "label": profile["label"],
                    "db_root": root,
                    "active": False,
                    "available": False,
                },
            )
        for root, account in rows.items():
            profile = profiles.get(root)
            display = self.state.account_display(root, saved)
            # Preserve a friendly name supplied by an alternate discovery source.
            if display["name_known"] or account.get("label") == Path(root).parent.name:
                account.update(display)
            else:
                account.update({k: v for k, v in display.items() if k != "label"})
            account.update(
                profile_id=profile["id"] if profile else "",
                credential_saved=bool(profile and profile.get("key_ref")),
                selected=bool(profile and profile["id"] == saved["active_profile"]),
            )
        current_rows = [a for a in rows.values() if a["active"]]
        current = current_rows[0] if len(current_rows) == 1 else None
        selected = next((a for a in rows.values() if a["selected"]), None)
        matches = bool(
            current and selected and current["db_root"] == selected["db_root"]
        )
        if unavailable:
            code, message = (
                "unavailable",
                "无法读取微信账号资料。已保存的账号仍列在下方；请允许完整磁盘访问权限，重新连接后刷新。",
            )
        elif not current:
            code, message = (
                "unconfirmed",
                "尚未确认微信当前登录账号。请正常登录后刷新；若已登录，请检查资料读取权限。",
            )
        elif not current["credential_saved"]:
            code, message = (
                "needs_credentials",
                "当前登录账号尚未保存密钥。请连接此账号；之前账号的凭据和记录会保留。",
            )
        elif not matches:
            code, message = (
                "switch_required",
                "微信当前登录账号与本项目选择的账号不同。请验证并切换到当前账号后继续。",
            )
        else:
            code, message = "matched", "当前登录账号与本项目使用的账号一致。"
        return {
            "code": code,
            "message": message,
            "current": current,
            "selected": selected,
            "matches_current": matches,
            "accounts": list(rows.values()),
        }

    def refresh_account_names(self, root, quiet=False):
        from .account_names import read_identity

        profile = next(
            (p for p in self.state.load()["profiles"] if p["db_root"] == str(root)),
            None,
        )
        if not profile:
            raise ValueError(
                "请先连接并验证此账号，之后才能读取微信昵称和微信号；现在可以先填写本地备注"
            )
        try:
            reader = SnapshotReader(
                Config(
                    db_root=Path(root),
                    key_file=Path("/nonexistent"),
                    state_dir=self.state.home / "preview" / profile["id"],
                    key_ref=profile["key_ref"],
                    sqlcipher=default_sqlcipher(),
                )
            )
            identity = read_identity(reader, root)
            if not any(identity.values()):
                raise ValueError(
                    "这份本地资料中未找到可确认的本人昵称或微信号，可以先使用本地备注"
                )
            result = self.state.save_account_names(
                root, **identity, identity_profile_id=profile["id"]
            )
            return {**result, "message": "微信昵称与微信号已更新，本地备注会保留"}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            if not quiet:
                if isinstance(exc, PermissionError):
                    raise PermissionError(
                        "尚未获得读取微信资料的权限。请在权限帮助中完成授权并重新连接；本地备注仍可修改。"
                    ) from None
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise RuntimeError(
                        "读取微信资料超时，请稍后重试；本地备注仍可修改。"
                    ) from None
                raise
            self.state.save_account_names(root, identity_profile_id=profile["id"])
            return self.state.account_display(root)

    def require_current_account(self, profile=None):
        from .discovery import active_roots

        profile = profile or self.state.profile()
        roots = active_roots()
        if len(roots) != 1:
            raise ValueError(
                "尚未确认微信当前登录账号。请正常登录并在账号区域刷新；若已登录，请检查完整磁盘访问权限。"
            )
        if Path(roots[0]).resolve() != Path(profile["db_root"]).resolve():
            raise ValueError(
                "微信当前登录账号与本项目选择的账号不同。请在账号区域连接或切换当前账号，不能沿用旧账号配置。"
            )

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
        self.require_current_account()
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
            "account-name",
            "refresh-account-name",
            "import-key",
            "restore-key",
            "feishu-secret",
            "start",
            "stop",
            "task-review",
            "reset-cursor",
            "auto-connect",
            "connect-dsh",
            "finish-setup",
            "run-settings",
        }:
            with self.lock:
                return self._perform(operation, data)
        return self._perform(operation, data)

    def _perform(self, operation, data):
        if operation == "connect-dsh":
            if self.services.running():
                raise ValueError("请先在工作台暂停监控，再连接或更新模型")
            from .onboarding import prepare_dsh

            cost = prepare_dsh(self.state)
            bridge = self.install_dsh()
            ready = bool(bridge.get("ready"))
            return {
                "ready": ready,
                "cost_meter": cost,
                "checks": [
                    {"stage": "费用插件", "ok": bool(cost.get("installed"))},
                    {
                        "stage": "DSH 模型连接",
                        "ok": ready,
                        "message": bridge.get("hint", ""),
                    },
                ],
                "message": "DSH 与费用插件已连接"
                if ready
                else bridge.get("hint", "请在 DSH 完成模型配置"),
            }
        if operation == "auto-connect":
            if self.services.running():
                raise ValueError("请先暂停监控，再检查或更新连接")
            from .onboarding import connect_existing

            return connect_existing(self)
        if operation == "finish-setup":
            self.require_current_account()
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
                installed = login_file().exists()
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
        if operation == "account-status":
            return self.account_status()
        if operation in {"account-name", "refresh-account-name"}:
            root = str(Path(str(data.get("db_root", ""))).expanduser().resolve())
            if root not in {a["db_root"] for a in self.account_status()["accounts"]}:
                raise ValueError("请选择已识别的微信账号")
            if operation == "refresh-account-name":
                return self.refresh_account_names(root)
            return self.state.save_account_names(
                root, custom_label=data.get("label", "")
            )
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
            self.refresh_account_names(str(root), quiet=True)
            return {
                "connected": True,
                "verified": answer["verified"],
                "total": answer["total"],
            }
        if operation == "verify-key":
            p = self.state.profile()
            result = native_request(
                {
                    "operation": "verify",
                    "db_root": p["db_root"],
                    "key_ref": p["key_ref"],
                }
            )
            if result.get("all_verified"):
                self.refresh_account_names(p["db_root"], quiet=True)
            return result
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
            self.refresh_account_names(str(root), quiet=True)
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
            switching = (
                "active_profile" in values
                and values["active_profile"] != self.state.load()["active_profile"]
            )
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
            if "active_profile" in values:
                profile = next(
                    p
                    for p in self.state.load()["profiles"]
                    if p["id"] == values["active_profile"]
                )
                self.require_current_account(profile)
                verified = native_request(
                    {
                        "operation": "verify",
                        "db_root": profile["db_root"],
                        "key_ref": profile["key_ref"],
                    }
                )
                if not verified.get("all_verified"):
                    raise ValueError(
                        "此账号已保存的密钥未通过数据库验证，请先在微信凭据中修复连接。原配置未更改。"
                    )
                if switching:
                    values.update(
                        chat_id="",
                        group_name="",
                        send_enabled=False,
                        autostart_monitor=False,
                    )
            previous = self.state.load()
            if any(
                k in values and values[k] != previous[k]
                for k in ["feishu_transport", "feishu_app_id"]
            ):
                values.update(
                    feishu_chat_id="", feishu_chat_name="", send_enabled=False
                )
            self.state.update(**values)
            if "active_profile" in values:
                self.refresh_account_names(profile["db_root"], quiet=True)
            if switching:
                if self.preview_reader is not None:
                    self.preview_reader.close()
                self.preview_reader = None
                return {
                    "saved": True,
                    "message": "已验证并切换账号。请重新选择微信会话，确认飞书接收群后再开启投递与监控。",
                }
            return {"saved": True}
        if operation == "feishu-secret":
            if self.services.running():
                raise ValueError("请先暂停监听再修改飞书凭据")
            if not re.fullmatch(r"cli_[A-Za-z0-9]+", str(data.get("app_id", ""))):
                raise ValueError("飞书 App ID 无效")
            answer = native_request(
                {"operation": "store_secret", "secret": data["secret"]}
            )
            previous = self.state.load()
            destination = {}
            if (
                previous["feishu_app_id"] != data["app_id"]
                or previous["feishu_transport"] != "openapi"
            ):
                destination = {
                    "feishu_chat_id": "",
                    "feishu_chat_name": "",
                    "send_enabled": False,
                }
            self.state.update(
                **destination,
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
            text = "【NoteBridge 连通性测试】机器人接入正常。本消息由你在本地应用中点击测试后发送。"
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
            self.require_current_account()
            if self.state.load().get("brand_bridge_pending"):
                try:
                    with (
                        Path(self.state.profile()["db_root"]) / "contact/contact.db"
                    ).open("rb"):
                        pass
                except PermissionError:
                    raise PermissionError(
                        "请在完整磁盘访问权限中允许 NoteBridge，再点击重新连接；已有密钥无需重取。"
                    ) from None
                if not self.install_dsh().get("ready"):
                    raise RuntimeError(
                        "桥接迁移尚未就绪，请检查 DSH；原有模型渠道和费用账本已保留"
                    )
                self.state.update(brand_bridge_pending=False)
            result = self.services.start()
            if result.get("running"):
                self.state.update(migration_resume_requested=False)
            return result
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
        if operation == "connection-check":
            from .onboarding import check_first_connection

            return check_first_connection(
                self, str(data.get("db_root", "")), data.get("discover_account") is True
            )
        if operation == "capture-preflight":
            from .keyflow import KeyFlow

            return KeyFlow(self.state).preflight(
                str(data.get("db_root", "")), data.get("discover_account") is True
            )
        if operation == "capture-start":
            if self.services.running():
                raise ValueError("请先暂停监听再开始首次取钥")
            from .keyflow import KeyFlow

            return KeyFlow(self.state).start(
                str(data.get("db_root", "")), data.get("discover_account") is True
            )
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

            result = KeyFlow(self.state).status(str(data["capture_id"]))
            profile = next(
                (
                    p
                    for p in self.state.load()["profiles"]
                    if p["db_root"] == result.get("db_root")
                ),
                None,
            )
            if result.get("phase") == "completed" and profile:
                names = (
                    self.state.load()
                    .get("account_names", {})
                    .get(profile["db_root"], {})
                )
                if names.get("identity_profile_id") != profile["id"]:
                    self.refresh_account_names(profile["db_root"], quiet=True)
                result["account_label"] = self.state.account_display(
                    profile["db_root"]
                )["label"]
            return result
        if operation == "diagnostics":
            s = self.state.load()
            return {
                "version": "0.4.4",
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
        from .migration import migrate_dsh_storage, validate_move

        validate_move(home / "storages/wx-reply-agent", home / "storages/notebridge")
        source = Path(__file__).parent / "assets/dsh/index.mjs"
        if not source.exists():
            source = Path(__file__).resolve().parents[1] / "integrations/dsh/index.mjs"
        content = source.read_bytes()
        release = hashlib.sha256(content).hexdigest()[:16]
        current = {}
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
        owned_pattern = (
            rf"\s*- id: (?:{re.escape(BRIDGE_ID)}|{re.escape(LEGACY_BRIDGE_ID)})\s*$"
        )
        matches = [i for i, line in enumerate(lines) if re.match(owned_pattern, line)]
        if len(matches) > 1:
            raise ValueError(
                "同时发现多份新旧桥接条目，请先核对并只保留本项目条目；未修改其他配置"
            )
        start = next(
            (i for i, line in enumerate(lines) if re.match(owned_pattern, line)),
            None,
        )
        indent, end = 4, len(lines)
        if start is not None:
            deadline = time.monotonic() + 30
            while current.get("active", 0):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "DSH 仍在完成已有回复任务，请完成后重试迁移；桥接尚未停用"
                    )
                time.sleep(0.5)
                current = self.dsh().request("/v1/status")
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
                    " " * indent + "- id: notebridge\n",
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
        migrate_dsh_storage(home)
        row = [
            " " * indent + "- id: notebridge\n",
            " " * (indent + 2) + "name: " + target.as_uri() + "\n",
            " " * (indent + 2) + "config:\n",
            " " * (indent + 4) + "port: " + str(self.state.load()["dsh_port"]) + "\n",
        ]
        if start is not None:
            # Preserve unrelated settings that may have changed while waiting for disposal.
            fresh = patch.read_text().splitlines(keepends=True)
            current = next(
                (i for i, line in enumerate(fresh) if re.match(owned_pattern, line)),
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
                if (
                    status.get("build") == VERSION
                    and status.get("source_digest", "")[:16] == release
                ):
                    return {"installed": True, "build": release, "ready": True}
            except (OSError, RuntimeError):
                pass
            time.sleep(0.5)
        return {
            "installed": True,
            "ready": False,
            "hint": "请在 DSH 中检查插件状态，并确认 dsh-cost-meter 已安装",
        }
