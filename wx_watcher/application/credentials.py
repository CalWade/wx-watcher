"""Application commands for Credentials; concrete adapters are supplied by composition."""

from __future__ import annotations

from .ports import CredentialsContext


class CredentialsUseCases:
    operations = (
        "import-key",
        "verify-key",
        "backup-key",
        "restore-key",
        "capture-preflight",
        "capture-start",
        "capture-cancel",
        "capture-login-ready",
        "capture-sessions",
        "capture-recover",
        "capture-status",
    )

    def __init__(self, context: CredentialsContext):
        self.context = context

    def handle(self, operation, data):
        if operation == "import-key":
            if self.context.services.running():
                raise ValueError("请先暂停监听再连接其他账号")
            root = self.context.canonical_root(str(data["db_root"]))
            raw = self.context.load_key_file(str(data["key_file"]))
            answer = self.context.credential_request(
                {"operation": "store", "db_root": str(root), "key_hex": raw.hex()}
            )
            self.context.state.add_profile(
                root,
                answer["key_ref"],
                str(data.get("label", "")),
                {k: v for k, v in answer.items() if k not in {"key_ref"}},
            )
            self.context.close_preview()
            self.context.refresh_account_names(str(root), quiet=True)
            return {
                "connected": True,
                "verified": answer["verified"],
                "total": answer["total"],
            }
        if operation == "verify-key":
            p = self.context.state.profile()
            result = self.context.credential_request(
                {
                    "operation": "verify",
                    "db_root": p["db_root"],
                    "key_ref": p["key_ref"],
                }
            )
            if result.get("all_verified"):
                self.context.refresh_account_names(p["db_root"], quiet=True)
            return result
        if operation == "backup-key":
            return self.context.credential_request(
                {
                    "operation": "export_backup",
                    "key_ref": self.context.state.profile()["key_ref"],
                    "password": data["password"],
                }
            )
        if operation == "restore-key":
            if self.context.services.running():
                raise ValueError("请先暂停监听再恢复账号凭据")
            root = self.context.canonical_root(str(data["db_root"]))
            result = self.context.credential_request(
                {
                    "operation": "import_backup",
                    "db_root": str(root),
                    "backup": data["backup"],
                    "password": data["password"],
                }
            )
            self.context.state.add_profile(
                root, result["key_ref"], str(data.get("label", "")), result
            )
            self.context.refresh_account_names(str(root), quiet=True)
            return {
                "connected": True,
                "verified": result["verified"],
                "total": result["total"],
            }
        if operation == "capture-preflight":
            return self.context.capture.preflight(
                str(data.get("db_root", "")), data.get("discover_account") is True
            )
        if operation == "capture-start":
            if self.context.services.running():
                raise ValueError("请先暂停监听再开始首次取钥")

            return self.context.capture.start(
                str(data.get("db_root", "")), data.get("discover_account") is True
            )
        if operation == "capture-cancel":
            return self.context.capture.cancel(str(data["capture_id"]))
        if operation == "capture-login-ready":
            return self.context.capture.login_page_ready(str(data["capture_id"]))
        if operation == "capture-sessions":
            return self.context.capture.sessions()
        if operation == "capture-recover":
            return self.context.capture.recover(str(data["capture_id"]))
        if operation == "capture-status":
            result = self.context.capture.status(str(data["capture_id"]))
            profile = next(
                (
                    p
                    for p in self.context.state.load()["profiles"]
                    if p["db_root"] == result.get("db_root")
                ),
                None,
            )
            if result.get("phase") == "completed" and profile:
                names = (
                    self.context.state.load()
                    .get("account_names", {})
                    .get(profile["db_root"], {})
                )
                if names.get("identity_profile_id") != profile["id"]:
                    self.context.refresh_account_names(profile["db_root"], quiet=True)
                result["account_label"] = self.context.state.account_display(
                    profile["db_root"]
                )["label"]
            return result
        raise ValueError("未知操作")
