"""Guided capture session. All privileged work stays in one bounded native job."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from .appstate import AppState, atomic_json, app_home
from .credentials import native_request, native_tool
from .source import load_master, private_dir


class KeyFlow:
    lock = threading.RLock()

    def __init__(self, state: AppState):
        self.state = state
        # Privileged helper accepts only this canonical location, never arbitrary roots.
        self.jobs = Path.home()/"Library/Application Support/WXWatcher/keytool/jobs"

    def preflight(self, root: str):
        result = native_request({"operation": "capture_preflight"})
        root_path = Path(root).expanduser().resolve()
        result["database_readable"] = (root_path/"contact/contact.db").is_file()
        result["ready"] = result["ready"] and result["database_readable"]
        result["steps"] = ["环境检查", "账号确认", "官方客户端备份", "监听就绪", "退出并重新登录", "密钥验证", "恢复客户端", "Keychain 保存"]
        return result

    def start(self, root: str):
        with self.lock:
            report = self.preflight(root)
            if not report["ready"]:
                raise ValueError("取钥前置条件未通过，请先查看环境检查；当前仅允许已验证的微信构建")
            private_dir(self.jobs)
            for job in self.jobs.iterdir():
                if (job/"progress.json").exists():
                    old = json.loads((job/"progress.json").read_text())
                    if old.get("phase") not in {"completed", "cancelled", "failed", "recovered"}:
                        raise ValueError("有未完成的取钥或恢复任务，请先处理它")
            id = str(uuid.uuid4())
            directory = self.jobs/id
            private_dir(directory)
            samples = directory/"samples"
            private_dir(samples)
            root_path = Path(root).expanduser().resolve()
            count = 0
            for source in sorted(root_path.rglob("*.db")):
                if source.is_symlink() or not source.is_file(): continue
                with source.open("rb") as f: page = f.read(4096)
                if len(page) != 4096 or page.startswith(b"SQLite format 3"):
                    continue
                target = samples/(str(count)+".db")
                target.write_bytes(page)
                target.chmod(0o600)
                count += 1
            if not count: raise ValueError("未发现可用于校验的加密数据库首部")
            atomic_json(directory/"request.json", {"db_root": str(root_path), "uid": os.getuid(), "count": count, "owner_pid": os.getpid()})
            atomic_json(directory/"progress.json", {"phase": "authorization", "message": "请在系统窗口授权本次取钥与恢复任务"})
            # No password is collected or passed. AppleScript invokes the OS authorization UI once.
            executable = str(native_tool())
            import shlex
            shell = " ".join(shlex.quote(s) for s in [executable, "--capture-job", str(directory)])
            script = "do shell script " + json.dumps(shell) + " with administrator privileges"
            process = subprocess.Popen(["/usr/bin/osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def finish():
                code = process.wait()
                with self.lock:
                    current = json.loads((directory/"progress.json").read_text())
                    if current.get("phase") == "authorization":
                        atomic_json(directory/"progress.json", {"phase": "failed", "message": "系统授权未完成，未开始修改微信"})
            threading.Thread(target=finish, daemon=True).start()
            return {"capture_id": id, "phase": "authorization"}

    def sessions(self):
        if not self.jobs.exists(): return []
        result = []
        for directory in sorted(self.jobs.iterdir()):
            try:
                value = json.loads((directory/"progress.json").read_text())
                result.append({"capture_id": directory.name, **value})
            except (OSError, ValueError): pass
        return result[-20:]

    def recover(self, id):
        directory = self.directory(id)
        backup = directory/"WeChat.original.app"
        if not backup.is_dir(): raise ValueError("未找到本次任务的官方客户端备份")
        import shlex
        shell = " ".join(shlex.quote(s) for s in [str(native_tool()), "--capture-job", str(directory), "--recover"])
        subprocess.Popen(["/usr/bin/osascript", "-e", "do shell script "+json.dumps(shell)+" with administrator privileges"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"capture_id": id, "recovery_requested": True}

    def cancel_owned(self):
        if not self.jobs.exists(): return
        for directory in self.jobs.iterdir():
            try:
                request = json.loads((directory/"request.json").read_text())
                progress = json.loads((directory/"progress.json").read_text())
                if request.get("owner_pid") == os.getpid() and progress.get("phase") not in {"completed", "failed", "cancelled", "recovered"}:
                    self.cancel(directory.name)
            except (OSError, ValueError): pass

    def directory(self, id):
        if str(uuid.UUID(id)) != id: raise ValueError("取钥任务 ID 无效")
        return self.jobs/id

    def status(self, id):
        with self.lock:
            directory = self.directory(id)
            progress = json.loads((directory/"progress.json").read_text())
            if progress.get("phase") == "captured":
                request = json.loads((directory/"request.json").read_text())
                key_file = directory/"pending-key"
                raw = load_master(key_file)
                saved = native_request({"operation": "store", "key_hex": raw.hex(), "db_root": request["db_root"]})
                self.state.add_profile(Path(request["db_root"]), saved["key_ref"], verification={k:v for k,v in saved.items() if k != "key_ref"})
                key_file.unlink()
                progress.update(phase="completed", message="密钥已验证并存入 Keychain，官方微信已恢复", verified=saved["verified"], total=saved["total"])
                atomic_json(directory/"progress.json", progress)
            return progress

    def cancel(self, id):
        directory = self.directory(id)
        file = directory/"cancel"
        file.touch(mode=0o600)
        return {"cancel_requested": True, "message": "正在请求解除调试并恢复客户端，请等待恢复结果"}
