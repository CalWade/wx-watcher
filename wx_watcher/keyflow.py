"""Guided capture session. All privileged work stays in one bounded native job."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from .appstate import AppState, atomic_json
from .credentials import native_request, native_tool
from .source import load_master, private_dir


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


class KeyFlow:
    lock = threading.RLock()

    def __init__(self, state: AppState):
        self.state = state
        # Privileged helper accepts only this canonical location, never arbitrary roots.
        self.jobs = Path.home() / "Library/Application Support/NoteBridge/setup/jobs"

    def preflight(self, root: str):
        root_path = Path(root).expanduser().resolve()
        # Probe in the actual bundled native tool. A source Python process can
        # inherit different privacy permissions and must not stand in for it.
        result = native_request(
            {"operation": "capture_preflight", "db_root": str(root_path)}
        )
        from .discovery import active_roots

        active = active_roots()
        result["wechat_logged_in"] = bool(active)
        result["active_account_matches"] = len(active) == 1 and active[0] == root_path
        result["ready"] = (
            result["ready"]
            and result.get("database_readable", False)
            and result.get("application_manageable", False)
            and (not active or result["active_account_matches"])
        )
        if active and not result["active_account_matches"]:
            result["account_hint"] = (
                "请正常登录要取钥的账号，并选择当前微信实际打开的 db_storage，避免用旧账号资料验证"
            )
        elif not active:
            result["account_hint"] = (
                "微信可停在登录页准备。工具重开微信后，请先停在登录页并点击向导确认，等监听就绪再登录所选账号。"
            )
        elif not result.get("database_readable", False) and not result.get(
            "permission_hint"
        ):
            result["permission_hint"] = (
                "取钥工具未确认数据访问权限，请更新当前安装版后重新检查。"
            )
        result["steps"] = [
            "环境检查",
            "账号确认",
            "数据访问与 App 管理权限",
            "官方客户端备份",
            "停在登录页并确认",
            "监听就绪",
            "登录所选账号",
            "密钥验证",
            "恢复客户端",
            "Keychain 保存",
        ]
        return result

    def start(self, root: str):
        with self.lock:
            report = self.preflight(root)
            if not report["ready"]:
                raise ValueError(
                    report.get("permission_hint")
                    or report.get("account_hint")
                    or "取钥前置条件未通过，请先查看环境检查；当前仅允许已验证的微信构建"
                )
            private_dir(self.jobs)
            for job in self.jobs.iterdir():
                if (job / "progress.json").exists():
                    old = json.loads((job / "progress.json").read_text())
                    if old.get("phase") not in {
                        "completed",
                        "cancelled",
                        "failed",
                        "recovered",
                    }:
                        raise ValueError("有未完成的取钥或恢复任务，请先处理它")
            id = str(uuid.uuid4())
            directory = self.jobs / id
            private_dir(directory)
            samples = directory / "samples"
            private_dir(samples)
            root_path = Path(root).expanduser().resolve()
            count = 0
            for source in sorted(root_path.rglob("*.db")):
                if source.is_symlink() or not source.is_file():
                    continue
                with source.open("rb") as f:
                    page = f.read(4096)
                if len(page) != 4096 or page.startswith(b"SQLite format 3"):
                    continue
                target = samples / (str(count) + ".db")
                target.write_bytes(page)
                target.chmod(0o600)
                count += 1
            if not count:
                raise ValueError("未发现可用于校验的加密数据库首部")
            atomic_json(
                directory / "request.json",
                {
                    "db_root": str(root_path),
                    "uid": os.getuid(),
                    "count": count,
                    "owner_pid": os.getpid(),
                },
            )
            atomic_json(
                directory / "progress.json",
                {
                    "phase": "authorization",
                    "message": "请在系统窗口授权本次取钥与恢复任务",
                },
            )
            # No password is collected or passed. AppleScript invokes the OS authorization UI once.
            executable = str(native_tool())
            import shlex

            shell = " ".join(
                shlex.quote(s) for s in [executable, "--capture-job", str(directory)]
            )
            script = (
                "do shell script "
                + json.dumps(shell)
                + " with administrator privileges"
            )
            process = subprocess.Popen(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=directory,
            )

            def finish():
                process.wait()
                with self.lock:
                    current = json.loads((directory / "progress.json").read_text())
                    if current.get("phase") == "authorization":
                        atomic_json(
                            directory / "progress.json",
                            {
                                "phase": "failed",
                                "message": "系统授权未完成，未开始修改微信",
                            },
                        )
                    elif current.get("phase") not in {
                        "captured",
                        "completed",
                        "cancelled",
                        "failed",
                        "recovered",
                        "recovery_required",
                    }:
                        atomic_json(
                            directory / "progress.json",
                            {
                                "phase": "recovery_required",
                                "message": "取钥任务中断，请先检查并恢复官方客户端。",
                            },
                        )

            threading.Thread(target=finish, daemon=True).start()
            return {"capture_id": id, "phase": "authorization"}

    def sessions(self):
        if not self.jobs.exists():
            return []
        result = []
        for directory in sorted(self.jobs.iterdir()):
            try:
                value = json.loads((directory / "progress.json").read_text())
                if value.get("phase") not in {
                    "completed",
                    "captured",
                    "cancelled",
                    "failed",
                    "recovered",
                    "recovery_required",
                }:
                    request = json.loads((directory / "request.json").read_text())
                    if not process_alive(
                        request.get("owner_pid")
                    ) and not process_alive(value.get("helper_pid")):
                        value.update(
                            phase="recovery_required"
                            if (directory / "WeChat.original.app").exists()
                            else "failed",
                            message="上次取钥任务已中断，请先检查并恢复官方客户端",
                        )
                        atomic_json(directory / "progress.json", value)
                result.append({"capture_id": directory.name, **value})
            except (OSError, ValueError):
                pass
        return result[-20:]

    def recover(self, id):
        directory = self.directory(id)
        backup = directory / "WeChat.original.app"
        if not backup.is_dir():
            raise ValueError("未找到本次任务的官方客户端备份")
        progress = json.loads((directory / "progress.json").read_text())
        if progress.get("phase") != "recovery_required":
            raise ValueError("仅未完成恢复的任务可使用恢复入口，请先查看当前进度")
        if process_alive(progress.get("helper_pid")):
            raise ValueError("上一次系统任务仍在退出或恢复中，请稍候再试")
        import shlex

        shell = " ".join(
            shlex.quote(s)
            for s in [str(native_tool()), "--capture-job", str(directory), "--recover"]
        )
        subprocess.Popen(
            [
                "/usr/bin/osascript",
                "-e",
                "do shell script "
                + json.dumps(shell)
                + " with administrator privileges",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=directory,
        )
        return {"capture_id": id, "recovery_requested": True}

    def cancel_owned(self):
        if not self.jobs.exists():
            return
        for directory in self.jobs.iterdir():
            try:
                request = json.loads((directory / "request.json").read_text())
                progress = json.loads((directory / "progress.json").read_text())
                if request.get("owner_pid") == os.getpid() and progress.get(
                    "phase"
                ) not in {"completed", "failed", "cancelled", "recovered"}:
                    self.cancel(directory.name)
            except (OSError, ValueError):
                pass

    def directory(self, id):
        if str(uuid.UUID(id)) != id:
            raise ValueError("取钥任务 ID 无效")
        return self.jobs / id

    def status(self, id):
        with self.lock:
            directory = self.directory(id)
            progress = json.loads((directory / "progress.json").read_text())
            if progress.get("phase") == "captured":
                request = json.loads((directory / "request.json").read_text())
                key_file = directory / "pending-key"
                raw = load_master(key_file)
                saved = native_request(
                    {
                        "operation": "store",
                        "key_hex": raw.hex(),
                        "db_root": request["db_root"],
                    }
                )
                self.state.add_profile(
                    Path(request["db_root"]),
                    saved["key_ref"],
                    verification={k: v for k, v in saved.items() if k != "key_ref"},
                )
                key_file.unlink()
                progress.update(
                    phase="completed",
                    message="密钥已验证并存入 Keychain，官方微信已恢复",
                    verified=saved["verified"],
                    total=saved["total"],
                )
                atomic_json(directory / "progress.json", progress)
            return progress

    def cancel(self, id):
        directory = self.directory(id)
        file = directory / "cancel"
        file.touch(mode=0o600)
        return {
            "cancel_requested": True,
            "message": "正在请求解除调试并恢复客户端，请等待恢复结果",
        }

    def login_page_ready(self, id):
        directory = self.directory(id)
        progress = json.loads((directory / "progress.json").read_text())
        if progress.get("phase") != "awaiting_login_page":
            raise ValueError("当前不在登录页确认阶段，请按最新进度操作")
        atomic_json(directory / "login-page-ready", {"confirmed": True})
        return {"message": "正在安装取钥监听，请等待明确就绪提示后再点击微信登录。"}
