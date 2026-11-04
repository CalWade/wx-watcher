from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


def default_sqlcipher() -> str:
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "bin/sqlcipher"
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    return (
        os.environ.get("WX_SQLCIPHER", "")
        or shutil.which("sqlcipher")
        or "/opt/homebrew/bin/sqlcipher"
    )


@dataclass(frozen=True)
class Config:
    db_root: Path | None
    key_file: Path
    state_dir: Path
    group_name: str = ""
    chat_id: str = ""
    backend: str = "sqlcipher"
    sqlcipher: str = "/opt/homebrew/bin/sqlcipher"
    poll_interval: float = 1.0
    port: int = 8765
    key_ref: str = ""
    keytool: str = ""
    retention_days: int = 0

    def __post_init__(self):
        state = self.state_dir.resolve()
        protected = Path.home() / "Library/Containers/com.tencent.xinWeChat"
        if state in {Path.home().resolve(), Path("/")} or state.is_relative_to(
            protected
        ):
            raise ValueError(
                "state_dir 必须使用独立项目目录，不能指向用户根目录或微信容器"
            )
        if self.db_root:
            source = self.db_root.resolve()
            if state.is_relative_to(source) or source.is_relative_to(state):
                raise ValueError("state_dir 与微信源数据库目录必须相互独立")

    @classmethod
    def load(cls, path: Path) -> Config:
        data = tomllib.loads(path.read_text())
        project = path.resolve().parent.parent

        def local(value: str) -> Path:
            p = Path(value).expanduser()
            return p.resolve() if p.is_absolute() else (project / p).resolve()

        result = cls(
            db_root=local(data["db_root"]) if data.get("db_root") else None,
            key_file=local(data.get("key_file", ".runtime/master-key.txt")),
            state_dir=local(data.get("state_dir", ".runtime")),
            group_name=data.get("group_name", cls.group_name),
            chat_id=data.get("chat_id", ""),
            backend=data.get("backend", "sqlcipher"),
            sqlcipher=data.get("sqlcipher") or default_sqlcipher(),
            poll_interval=float(data.get("poll_interval", 1)),
            port=int(data.get("port", 8765)),
            key_ref=data.get("key_ref", ""),
            keytool=data.get("keytool", ""),
            retention_days=int(data.get("retention_days", 0)),
        )
        if result.backend not in {"sqlcipher", "sqlite"}:
            raise ValueError("backend 必须是 sqlcipher 或离线测试用的 sqlite")
        if result.poll_interval < 0.2 or not 1 <= result.port <= 65535:
            raise ValueError("轮询间隔至少 0.2 秒，端口必须有效")
        if not 0 <= result.retention_days <= 10000:
            raise ValueError("retention_days 必须介于 0 和 10000")
        if result.chat_id and not re.fullmatch(
            r"[A-Za-z0-9_.@-]{1,256}", result.chat_id
        ):
            raise ValueError("chat_id 必须是原生微信会话 ID")
        return result


def discover_root(explicit: Path | None) -> Path:
    if explicit:
        root = explicit
        list(root.iterdir())  # 保留真实的权限异常；不让 glob 把拒绝访问伪装成空目录。
    else:
        base = (
            Path.home()
            / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        )
        roots = [
            p / "db_storage"
            for p in base.iterdir()
            if p.is_dir() and (p / "db_storage").is_dir()
        ]
        if len(roots) != 1:
            raise ValueError(f"发现 {len(roots)} 个账号，请明确配置 db_root")
        root = roots[0]
    if not (root / "contact/contact.db").is_file():
        raise FileNotFoundError("该 db_storage 中没有 contact/contact.db")
    return root
