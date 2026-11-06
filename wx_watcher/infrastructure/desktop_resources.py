from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ..config import Config, default_sqlcipher
from ..credentials import native_request
from ..source import SnapshotReader


class DesktopResources:
    def __init__(self, state, dsh_client):
        self.state = state
        self.dsh = dsh_client
        self.preview_reader = None

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
            checks["agent"] = self.agent_status()
        except (OSError, RuntimeError):
            checks["agent"] = {"ready": False}
        return checks

    def accounts(self):
        base = (
            Path.home()
            / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        )
        try:
            from ..discovery import active_roots

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

    def refresh_account_names(self, root, quiet=False):
        from ..account_names import read_identity

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

    def _install_dsh(self):
        from .agents.desktop import DshDesktop

        return DshDesktop(self.state, self.dsh).install()

    def prepare_agent(self, prepare_cost, install_bridge):
        from .agents.desktop import DshDesktop

        return DshDesktop.prepare(prepare_cost, install_bridge)

    @staticmethod
    def conversations(reader, query=""):
        reader = reader
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

    @staticmethod
    def active_roots():
        from ..discovery import active_roots

        return active_roots()
