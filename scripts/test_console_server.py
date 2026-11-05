"""Synthetic browser fixture server; never connects to personal data or external services."""

import argparse
import tempfile
from pathlib import Path

from wx_watcher.controller import Controller
from wx_watcher.desktop import create_desktop


class FakeServices:
    def __init__(self):
        self.active = False

    def running(self):
        return self.active

    def status(self):
        return {
            "running": self.active,
            "reader": {"state": "ready" if self.active else "stopped"},
        }

    def start(self):
        self.active = True
        return self.status()

    def stop(self):
        self.active = False
        return self.status()


class FixtureController(Controller):
    def __init__(self, directory, fresh=False):
        super().__init__(directory)
        self.services = FakeServices()
        self.capture_phase = "idle"
        self.state.update(
            profiles=[
                {
                    "id": "fixture",
                    "label": "合成测试账号",
                    "db_root": "/example/db_storage",
                    "key_ref": "fixture-reference",
                    "verification": {"verified": 2, "total": 2},
                }
            ],
            active_profile="fixture",
        )
        if fresh:
            self.state.update(profiles=[], active_profile="")

    def records(self):
        return [
            {
                "id": "fixture",
                "status": "sent",
                "group": "测试群",
                "created_at": "2026-01-01",
                "event": {
                    "content": '<img src=x onerror="window.injectionExecuted=true">培训时间？'
                },
                "proposal": {
                    "should_reply": True,
                    "suggested_reply": "请先内部确认培训时间。",
                    "reason": "资料未提供时间",
                    "needs_confirmation": ["具体时间"],
                },
            }
        ]

    def accounts(self):
        return [
            {"label": "合成当前账号", "db_root": "/example/db_storage", "active": True}
        ]

    def perform(self, operation, data):
        if operation == "auto-connect":
            return {
                "checks": [{"stage": "合成连接检查", "ok": True}],
                "conversations": [{"username": "filehelper", "name": "文件传输助手"}],
                "feishu_chats": [{"chat_id": "oc_fixture", "name": "合成测试群"}],
            }
        if operation == "run-settings":
            return super().perform(
                "settings",
                {
                    k: data[k]
                    for k in ["send_enabled", "autostart_monitor"]
                    if k in data
                },
            )
        if operation == "finish-setup":
            self.services.start()
            return {"running": True, "message": "合成链路已启动"}
        if operation == "conversations":
            return [{"username": "filehelper", "name": "文件传输助手"}]
        if operation == "doctor":
            return {"native": True, "reader": True, "synthetic": True}
        if operation == "accounts":
            return [
                {
                    "label": "合成当前账号",
                    "db_root": "/example/db_storage",
                    "active": True,
                }
            ]
        if operation == "capture-sessions":
            return []
        if operation == "connection-check":
            return {
                "ready": True,
                "db_root": "/example/db_storage",
                "account_label": "合成当前账号",
                "checks": [
                    {"id": id, "title": title, "ok": True, "message": message}
                    for id, title, message in [
                        ("client", "官方微信", "已找到官方签名客户端"),
                        ("version", "取钥兼容范围", "当前组合在已验证范围内"),
                        ("debugger", "调试工具", "兼容的 LLDB 已就绪"),
                        ("access", "微信资料读取权限", "未发现权限拒绝"),
                        ("database", "本地消息资料", "已找到可读取的账号数据库"),
                        ("account", "本次连接账号", "合成当前账号"),
                    ]
                ],
            }
        if operation == "capture-start":
            self.capture_phase = "awaiting_login_page"
            return {"capture_id": "synthetic-capture", "phase": self.capture_phase}
        if operation == "capture-login-ready":
            self.capture_phase = "ready_for_login"
            return {"message": "已确认合成登录页"}
        if operation == "capture-cancel":
            self.capture_phase = "cancelled"
            return {"cancel_requested": True}
        if operation == "capture-status":
            return {
                "phase": self.capture_phase,
                "message": "请先停在登录页确认"
                if self.capture_phase == "awaiting_login_page"
                else "监听已就绪，可以登录",
            }
        if operation == "install-dsh":
            return {"installed": True, "ready": True}
        if operation == "test-dsh":
            return {
                "output": "example",
                "tools": 0,
                "cost_meter": {"found": True},
                "usage": {"output": 5},
            }
        if operation == "feishu-chats":
            return {
                "ok": True,
                "data": {"chats": [{"chat_id": "oc_fixture", "name": "合成测试群"}]},
            }
        if operation == "test-feishu":
            return {"sent": True, "readback": True, "synthetic": True}
        return super().perform(operation, data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--port", type=int, default=19767)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        c = FixtureController(Path(tmp) / "app", fresh=args.fresh)
        server = create_desktop(c, "browser-fixture-token", args.port)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            c.pool.shutdown(wait=True)
