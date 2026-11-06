"""Application commands for Accounts; concrete adapters are supplied by composition."""

from pathlib import Path

from ..domain.accounts import AccountConnection, require_account_match
from .ports import AccountsContext


class AccountsUseCases:
    operations = (
        "accounts",
        "account-status",
        "account-name",
        "refresh-account-name",
        "conversations",
    )

    def __init__(self, context: AccountsContext):
        self.context = context

    def handle(self, operation, data):
        if operation == "accounts":
            return self.context.accounts()
        if operation == "account-status":
            return self.context.account_status()
        if operation in {"account-name", "refresh-account-name"}:
            root = str(self.context.canonical_root(str(data.get("db_root", ""))))
            if root not in {
                a["db_root"] for a in self.context.account_status()["accounts"]
            }:
                raise ValueError("请选择已识别的微信账号")
            if operation == "refresh-account-name":
                return self.context.refresh_account_names(root)
            return self.context.state.save_account_names(
                root, custom_label=data.get("label", "")
            )
        if operation == "conversations":
            return self.context.conversations(str(data.get("query", ""))[:100])
        raise ValueError("未知操作")

    def account_status(self):
        saved = self.context.state.load()
        unavailable = False
        try:
            discovered = self.context.accounts()
        except PermissionError:
            discovered, unavailable = [], True
        profiles = {
            str(self.context.canonical_root(p["db_root"])): p for p in saved["profiles"]
        }
        rows = {}
        for account in discovered:
            root = str(self.context.canonical_root(account["db_root"]))
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
            display = self.context.state.account_display(root, saved)
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
        connection = AccountConnection(
            current_root=current["db_root"] if current else None,
            selected_root=selected["db_root"] if selected else None,
            active_count=len(current_rows),
            credential_saved=bool(selected and selected["credential_saved"]),
            data_available=bool(selected and selected["available"]),
            access_denied=unavailable,
        )
        matches = connection.matches_current
        can_configure = connection.can_configure
        if unavailable:
            code, message = (
                "unavailable",
                "无法读取微信账号资料。已保存的账号仍列在下方；请允许完整磁盘访问权限，重新连接后刷新。",
            )
        elif can_configure and not current:
            code, message = (
                "local_ready",
                "凭据已保存，可继续选择本地会话并完成配置。开始监控前，请在官方微信中登录此账号并刷新状态；无需重新取钥。",
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
            "can_configure": can_configure,
            "accounts": list(rows.values()),
        }

    def require_current_account(self, profile=None):
        profile = profile or self.context.state.profile()
        require_account_match(
            str(self.context.canonical_root(profile["db_root"])),
            [
                str(self.context.canonical_root(str(root)))
                for root in self.context.active_roots()
            ],
            live=True,
        )

    def require_local_account(self):
        profile = self.context.state.profile()
        if not profile.get("key_ref"):
            raise ValueError("请先连接微信账号并验证密钥")
        require_account_match(
            str(self.context.canonical_root(profile["db_root"])),
            [
                str(self.context.canonical_root(str(root)))
                for root in self.context.active_roots()
            ],
            live=False,
        )

    def conversations(self, query=""):
        self.context.require_local_account()
        return self.context.read_conversations(query)
