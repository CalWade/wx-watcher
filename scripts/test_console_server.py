"""Synthetic browser fixture server; never connects to personal data or external services."""

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
    def __init__(self, directory):
        super().__init__(directory)
        self.services = FakeServices()
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
    with tempfile.TemporaryDirectory() as tmp:
        c = FixtureController(Path(tmp) / "app")
        server = create_desktop(c, "browser-fixture-token", 19767)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            c.pool.shutdown(wait=True)
