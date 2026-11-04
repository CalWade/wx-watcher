from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from .appstate import AppState, atomic_json
from .config import Config, default_sqlcipher
from .credentials import native_request, native_tool
from .feishu import Feishu
from .reply import LocalAPI, ReplyConfig
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
        self.jobs[id] = {"id": id, "operation": operation, "status": "running", "started": time.time()}

        def run():
            try:
                value = self.perform(operation, data)
                self.jobs[id].update(status="completed", result=value)
            except Exception as exc:
                # Only our own bounded diagnostic messages reach the interface.
                message = str(exc) if isinstance(exc, (ValueError, RuntimeError, FileNotFoundError, PermissionError)) else type(exc).__name__
                self.jobs[id].update(status="failed", error=message[:600])
            self.jobs[id]["finished"] = time.time()
        self.pool.submit(run)
        return {"job_id": id}

    def doctor(self):
        checks = {"python": sys.version.split()[0], "sqlcipher": shutil.which(default_sqlcipher()),
                  "lark_cli": shutil.which("lark-cli"), "platform": sys.platform}
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
        return LocalAPI(self.state.load()["dsh_port"], Path(os.environ.get("DSH_HOME", str(Path.home()/".dsh"))) / "storages/wx-reply-agent/token")

    def feishu(self):
        s = self.state.load()
        return Feishu(s["feishu_transport"], s["feishu_app_id"], s["feishu_secret_ref"])

    def accounts(self):
        base = Path.home()/"Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        try:
            return [{"label": p.name, "db_root": str(p/"db_storage")} for p in sorted(base.iterdir())
                    if p.is_dir() and (p/"db_storage/contact/contact.db").is_file()]
        except PermissionError:
            raise PermissionError("无法读取微信数据目录，请在系统隐私设置中允许本应用访问所需数据") from None
        except FileNotFoundError:
            return []

    def reader(self):
        p = self.state.profile()
        config = Config(db_root=Path(p["db_root"]), key_file=Path("/nonexistent"),
            state_dir=self.state.home/"preview"/p["id"], key_ref=p["key_ref"], sqlcipher=default_sqlcipher())
        if self.preview_reader is None or self.preview_reader.config != config:
            self.preview_reader = SnapshotReader(config)
        return self.preview_reader

    def conversations(self, query=""):
        reader = self.reader()
        rows = reader.rows(reader.config.db_root/"contact/contact.db",
            "SELECT username,nick_name,remark FROM contact WHERE username<>'' ORDER BY username LIMIT 10000;")
        result = []
        for r in rows:
            if r["username"].endswith("@chatroom") or r["username"] == "filehelper":
                r["name"] = r.get("remark") or r.get("nick_name") or r["username"]
                if not query or query.lower() in (r["name"]+r["username"]).lower():
                    result.append(r)
        if not any(r["username"] == "filehelper" for r in result) and (not query or query in "文件传输助手"):
            result.insert(0, {"username": "filehelper", "name": "文件传输助手"})
        return result[:200]

    def records(self):
        file = self.state.home/"config/reply.toml"
        if not file.exists():
            return []
        cfg = ReplyConfig.load(file)
        db = cfg.state_dir/"tasks.sqlite3"
        if not db.exists():
            return []
        with sqlite3.connect(db.as_uri()+"?mode=ro", uri=True) as c:
            return [json.loads(r[0]) for r in c.execute("SELECT body FROM tasks ORDER BY updated_at DESC LIMIT 100")]

    def perform(self, operation, data):
        if operation == "doctor": return self.doctor()
        if operation == "accounts": return self.accounts()
        if operation == "conversations": return self.conversations(str(data.get("query", ""))[:100])
        if operation == "import-key":
            if self.services.running(): raise ValueError("请先暂停监听再连接其他账号")
            root = Path(str(data["db_root"])).expanduser().resolve()
            raw = load_master(Path(str(data["key_file"])).expanduser())
            answer = native_request({"operation": "store", "db_root": str(root), "key_hex": raw.hex()})
            self.state.add_profile(root, answer["key_ref"], str(data.get("label", "")),
                                   {k:v for k,v in answer.items() if k not in {"key_ref"}})
            self.preview_reader = None
            return {"connected": True, "verified": answer["verified"], "total": answer["total"]}
        if operation == "verify-key":
            p = self.state.profile()
            return native_request({"operation": "verify", "db_root": p["db_root"], "key_ref": p["key_ref"]})
        if operation == "backup-key":
            return native_request({"operation": "export_backup", "key_ref": self.state.profile()["key_ref"], "password": data["password"]})
        if operation == "restore-key":
            root = Path(str(data["db_root"])).expanduser().resolve()
            result = native_request({"operation": "import_backup", "db_root": str(root),
                                      "backup": data["backup"], "password": data["password"]})
            self.state.add_profile(root, result["key_ref"], str(data.get("label", "")), result)
            return {"connected": True, "verified": result["verified"], "total": result["total"]}
        if operation == "settings":
            if self.services.running(): raise ValueError("请先暂停监听，再修改配置")
            allowed = {"active_profile", "chat_id", "group_name", "business_context", "knowledge_files", "reply_rules",
                "feishu_chat_id", "feishu_chat_name", "feishu_transport", "feishu_app_id", "dsh_port",
                "reader_port", "send_enabled", "autostart_monitor", "debounce_seconds", "max_daily_calls", "retention_days"}
            values = {k:v for k,v in data.items() if k in allowed}
            for k in ["business_context", "reply_rules", "group_name", "chat_id", "feishu_chat_id", "feishu_app_id"]:
                if k in values and (not isinstance(values[k], str) or len(values[k]) > 20000): raise ValueError("配置字段无效："+k)
            for k in ["dsh_port", "reader_port"]:
                if k in values and (type(values[k]) is not int or not 1024 <= values[k] <= 65535): raise ValueError("端口无效")
            if "chat_id" in values and not re.fullmatch(r"[A-Za-z0-9_.@-]{1,256}", values["chat_id"]): raise ValueError("会话 ID 无效")
            if "knowledge_files" in values:
                from .knowledge import select_passages
                select_passages(tuple(Path(p) for p in values["knowledge_files"]), "验证资料")
            if "send_enabled" in values and type(values["send_enabled"]) is not bool: raise ValueError("发送开关无效")
            self.state.update(**values)
            return {"saved": True}
        if operation == "feishu-secret":
            answer = native_request({"operation": "store_secret", "secret": data["secret"]})
            self.state.update(feishu_app_id=data["app_id"], feishu_secret_ref=answer["key_ref"], feishu_transport="openapi")
            return {"saved": True}
        if operation == "feishu-chats": return self.feishu().chats()
        if operation == "test-feishu":
            s = self.state.load()
            if not s["feishu_chat_id"]: raise ValueError("请先选择飞书接收群")
            result = self.feishu().send(s["feishu_chat_id"], "【WX Watcher 连通性测试】机器人接入正常。本消息由你在本地应用中点击测试后发送。", "wxt-"+uuid.uuid4().hex)
            message_id = result["data"]["message_id"]
            self.feishu().readback(message_id)
            return {"sent": True, "readback": True, "message_id": message_id}
        if operation == "test-dsh":
            api = self.dsh()
            id = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            job = api.request("/v1/jobs", {"id": id, "prompt": json.dumps({"conversation": "连接测试",
                "trigger_message": {"text": "请问培训几点开始？"}, "business_facts": "尚未提供培训时间，请提示内部确认。"}, ensure_ascii=False)})
            end = time.monotonic()+125
            while job["status"] in {"queued", "running"} and time.monotonic() < end:
                time.sleep(.5)
                job = api.request("/v1/jobs/"+id)
            if job["status"] != "completed": raise RuntimeError("模型测试未完成，请查看 DSH 会话")
            return {"output": job["output"], "usage": job.get("usage"), "cost_meter": job.get("cost_meter"),
                    "tools": job.get("request_tool_count", job.get("visible_tools")), "session_id": job["session_id"]}
        if operation == "install-dsh": return self.install_dsh()
        if operation == "start": return self.services.start()
        if operation == "stop": return self.services.stop()
        if operation == "capture-preflight":
            from .keyflow import KeyFlow
            return KeyFlow(self.state).preflight(str(data.get("db_root", "")))
        if operation == "capture-start":
            from .keyflow import KeyFlow
            return KeyFlow(self.state).start(str(data["db_root"]))
        if operation == "capture-cancel":
            from .keyflow import KeyFlow
            return KeyFlow(self.state).cancel(str(data["capture_id"]))
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
            return {"version": "0.2.0", "environment": self.doctor(), "services": self.services.status(),
                "profile_count": len(s["profiles"]), "destination_configured": bool(s["feishu_chat_id"]),
                "record_counts": {k:sum(r["status"]==k for r in self.records()) for k in ["sent", "draft", "needs_review", "delivery_review"]}}
        raise ValueError("未知操作")

    def install_dsh(self):
        home = Path(os.environ.get("DSH_HOME", str(Path.home()/".dsh")))
        profile = home/"profiles/desktop"
        if not profile.is_dir(): raise ValueError("未找到 DSH desktop 配置，请先安装并启动支持的 DSH 版本")
        source = Path(__file__).parent/"assets/dsh/index.mjs"
        if not source.exists(): source = Path(__file__).resolve().parents[1]/"integrations/dsh/index.mjs"
        content = source.read_bytes()
        release = hashlib.sha256(content).hexdigest()[:16]
        target = self.state.home/"integrations/dsh"/release/"index.mjs"
        private_dir(target.parent)
        target.write_bytes(content)
        patch = profile/"cordis.patch.yml"
        old = patch.read_text() if patch.exists() else ""
        backup = self.state.home/"backups"/("dsh-"+str(int(time.time()))+".yml")
        private_dir(backup.parent)
        backup.write_text(old)
        backup.chmod(0o600)
        lines = old.splitlines(keepends=True)
        start = next((i for i,line in enumerate(lines) if re.match(r"\s*- id: wx-reply-agent\s*$", line)), None)
        indent, end = 4, len(lines)
        if start is not None:
            indent = len(lines[start])-len(lines[start].lstrip())
            end = start+1
            while end < len(lines):
                line = lines[end]
                if line.strip() and len(line)-len(line.lstrip()) <= indent: break
                end += 1
            disabled = lines[:start] + [" "*indent+"- id: wx-reply-agent\n", " "*(indent+2)+"name: "+target.as_uri()+"\n", " "*(indent+2)+"disabled: true\n"] + lines[end:]
            patch.write_text("".join(disabled))
            time.sleep(1)
        row = [" "*indent+"- id: wx-reply-agent\n", " "*(indent+2)+"name: "+target.as_uri()+"\n",
               " "*(indent+2)+"config:\n", " "*(indent+4)+"port: "+str(self.state.load()["dsh_port"])+"\n"]
        updated = "".join(lines[:start]+row+lines[end:]) if start is not None else old+"\n- insert:\n"+"".join(row)
        patch.write_text(updated)
        endtime = time.monotonic()+20
        while time.monotonic() < endtime:
            try:
                status = self.dsh().request("/v1/status")
                if status.get("build") == "0.2.0": return {"installed": True, "build": release, "ready": True}
            except (OSError, RuntimeError): pass
            time.sleep(.5)
        return {"installed": True, "ready": False, "hint": "请在 DSH 中检查插件状态，并确认 dsh-cost-meter 已安装"}
