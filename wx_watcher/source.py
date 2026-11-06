"""源数据库仅以 rb 读取；合并、SQLCipher 查询和共享内存文件均发生在私有副本。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path

import zstandard

from ._vendor.wechat_decrypt import appmsg, message, wal_snapshot
from .config import Config, discover_root


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def load_master(path: Path) -> bytes:
    raw = path.read_text(encoding="ascii").strip()
    if raw.startswith("{"):
        obj = json.loads(raw)
        raw = obj.get("passphrase") or obj.get("master_key") or obj.get("raw_key") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        raise ValueError("主密钥文件格式无效（需要 32 字节；不会输出其内容）")
    if path.stat().st_mode & 0o077:
        raise PermissionError("密钥文件权限过宽，请将该文件设为仅本人可读写（600）")
    return bytes.fromhex(raw)


def fingerprint(path: Path) -> tuple:
    def stat(p: Path):
        try:
            s = p.stat()
            return s.st_ino, s.st_size, s.st_mtime_ns
        except FileNotFoundError:
            return None

    return stat(path), stat(Path(str(path) + "-wal"))


class SnapshotReader:
    def __init__(self, config: Config):
        self.config = config
        private_dir(config.state_dir)
        self.cache = config.state_dir / "snapshots"
        private_dir(self.cache)
        self._fingerprints: dict[Path, tuple] = {}
        self._keys: dict[bytes, bytes] = {}
        self._master: bytes | None = None
        self._master_signature: tuple | None = None
        self._lock = threading.RLock()

    def prepare(self, source: Path) -> Path:
        """复用上游经过 WAL 校验的快照读取；变化时原子替换私有加密快照。"""
        with self._lock:
            signature = fingerprint(source)
            dest = self.cache / (
                hashlib.sha256(str(source).encode()).hexdigest() + ".db"
            )
            if self._fingerprints.get(source) == signature and dest.exists():
                return dest
            data = wal_snapshot.read_snapshot(str(source))
            # 再次核对，避免把下一代源文件的元数据登记到上一代快照上。
            if fingerprint(source) != signature:
                raise RuntimeError("数据库正在写入，下一轮重试一致快照")
            fd, tmp = tempfile.mkstemp(dir=self.cache, prefix=".snapshot-")
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                os.replace(tmp, dest)
            finally:
                Path(tmp).unlink(missing_ok=True)
            self._fingerprints[source] = signature
            return dest

    def rows(self, source: Path, sql: str) -> list[dict]:
        with self._lock:
            return self._rows(self.prepare(source), sql)

    def _rows(self, snapshot: Path, sql: str) -> list[dict]:
        if self.config.backend == "sqlite":
            conn = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA query_only=ON")
                return [dict(r) for r in conn.execute(sql)]
            finally:
                conn.close()
        executable = shutil.which(self.config.sqlcipher)
        if not executable:
            raise FileNotFoundError("缺少 SQLCipher；不会用普通 SQLite 假装解密成功")
        if self.config.key_ref:
            signature = ("keychain", self.config.key_ref)
        else:
            info = self.config.key_file.stat()
            signature = info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode
        if self._master_signature != signature:
            if self.config.key_ref:
                from .credentials import load_reference

                self._master = load_reference(self.config.key_ref, self.config.keytool)
            else:
                self._master = load_master(self.config.key_file)
            self._master_signature = signature
            self._keys.clear()
        with snapshot.open("rb") as handle:
            salt = handle.read(16)
        if salt not in self._keys:
            # 与 wechat-decrypt/crypto.py 相同的 SQLCipher4 逐库派生参数。
            self._keys[salt] = hashlib.pbkdf2_hmac(
                "sha512", self._master, salt, 256000, 32
            )
        key = (self._keys[salt] + salt).hex()
        commands = (
            ".bail on\n"
            f"PRAGMA key = \"x'{key}'\";\n"
            "PRAGMA cipher_compatibility=4;\n"
            "PRAGMA query_only=ON;\n"
            ".headers on\n.mode json\n" + sql + "\n"
        )
        result = subprocess.run(
            [executable, "-readonly", str(snapshot)],
            input=commands,
            text=True,
            capture_output=True,
            timeout=8,
        )
        if result.returncode:
            # 不转发 SQLCipher 的原始 stderr，避免错误上下文回显 PRAGMA 密钥。
            raise RuntimeError(
                "SQLCipher 只读查询失败：请检查密钥、数据库版本和快照完整性"
            )
        output = result.stdout.strip()
        # 某些 SQLCipher 版本的 PRAGMA key 会先输出一行 ok。
        if output == "ok":
            return []
        if output.startswith("ok\n"):
            output = output[3:].lstrip()
        return json.loads(output) if output else []

    def close(self):
        with self._lock:
            self._master = None
            self._master_signature = None
            self._keys.clear()
            self._fingerprints.clear()


class WeChatSource:
    def __init__(self, config: Config):
        self.config = config
        self.reader = SnapshotReader(config)
        self.root: Path | None = None
        self.chat_id: str | None = None
        self.display_name = config.group_name

    def resolve(self) -> str:
        if self.chat_id:
            return self.chat_id
        self.root = discover_root(self.config.db_root).resolve()
        if self.config.chat_id == "filehelper":
            self.chat_id = "filehelper"
            self.display_name = "文件传输助手"
            return self.chat_id
        if not self.config.chat_id and not self.config.group_name:
            return self._select_sample_chat()

        def quote(s):
            return "'" + s.replace("'", "''") + "'"

        where = (
            "username=" + quote(self.config.chat_id)
            if self.config.chat_id
            else "(nick_name="
            + quote(self.config.group_name)
            + " OR remark="
            + quote(self.config.group_name)
            + ")"
        )
        matches = self.reader.rows(
            self.root / "contact/contact.db",
            "SELECT username FROM contact WHERE "
            + where
            + (";" if self.config.chat_id else " AND username LIKE '%@chatroom';"),
        )
        ids = sorted({r["username"] for r in matches})
        if len(ids) != 1:
            raise ValueError(
                f"目标群精确匹配 {len(ids)} 条，请核对群名或配置原生 chat_id"
            )
        self.chat_id = ids[0]
        return self.chat_id

    def _select_sample_chat(self) -> str:
        """Explicit empty target selects one real, nonempty conversation for validation."""
        contacts = self.reader.rows(
            self.root / "contact/contact.db",
            "SELECT username,nick_name,remark FROM contact WHERE username<>'' "
            "ORDER BY CASE WHEN username='filehelper' THEN 0 ELSE 1 END,username;",
        )
        directory = self.root / "message"
        shards = sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and re.fullmatch(r"message_\d+\.db", p.name)
        )
        for shard in shards:
            tables = {
                r["name"]
                for r in self.reader.rows(
                    shard, "SELECT name FROM sqlite_master WHERE type='table';"
                )
            }
            for contact in contacts:
                table = "Msg_" + hashlib.md5(contact["username"].encode()).hexdigest()
                if table in tables and self.reader.rows(
                    shard, f'SELECT 1 AS present FROM "{table}" LIMIT 1;'
                ):
                    self.chat_id = contact["username"]
                    self.display_name = (
                        contact.get("remark")
                        or contact.get("nick_name")
                        or self.chat_id
                    )
                    return self.chat_id
        raise ValueError("当前账号中没有找到有本地消息的会话")

    def shards(self) -> list[Path]:
        self.resolve()
        directory = self.root / "message"
        return sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and re.fullmatch(r"message_\d+\.db", p.name)
        )

    def read_shard(
        self, path: Path, after: int | dict[str, int] | None, limit: int = 1000
    ) -> tuple[str, list[dict]]:
        chat = self.resolve()
        with self.reader._lock:
            snapshot = self.reader.prepare(path)
            generation = self._generation(path, snapshot)
            if isinstance(after, dict):
                after = after.get(generation, 0)
            return generation, self._read_snapshot_shard(
                snapshot, path.name, chat, after, limit
            )

    def _read_snapshot_shard(
        self, snapshot: Path, shard: str, chat: str, after: int | None, limit: int
    ) -> list[dict]:
        def read(sql):
            return self.reader._rows(snapshot, sql)

        table = "Msg_" + hashlib.md5(chat.encode()).hexdigest()
        tables = read("SELECT name FROM sqlite_master WHERE type='table';")
        names = {r["name"] for r in tables}
        if table not in names:
            return []
        columns = {r["name"] for r in read(f'PRAGMA table_info("{table}");')}
        required = {"local_id", "create_time", "local_type", "message_content"}
        if not required <= columns:
            raise ValueError(
                f"{shard} 的消息结构尚未适配：{sorted(required - columns)}"
            )
        sender = (
            "COALESCE(n.user_name, '') AS sender"
            if "Name2Id" in names and "real_sender_id" in columns
            else "'' AS sender"
        )
        join = (
            " LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id"
            if sender.startswith("COALESCE")
            else ""
        )
        server = "m.server_id" if "server_id" in columns else "0 AS server_id"
        where = "" if after is None else f" WHERE m.local_id > {int(after)}"
        order = "DESC" if after is None else "ASC"
        rows = read(
            f"SELECT m.local_id, {server}, m.create_time, m.local_type, "
            f'hex(m.message_content) AS message_hex, {sender} FROM "{table}" m'
            f"{join}{where} ORDER BY m.local_id {order} LIMIT {int(limit)};",
        )
        return [self._format(row, shard, chat) for row in rows]

    def _generation(self, path: Path, snapshot: Path) -> str:
        # 数据库重建、换盐或换账号后不用旧水位跳过新消息。
        with snapshot.open("rb") as handle:
            header = handle.read(16)
        identity = str(self.root.resolve()).encode() + path.name.encode() + header
        return hashlib.sha256(identity).hexdigest()

    @staticmethod
    def _format(row: dict, shard: str, chat: str) -> dict:
        raw = bytes.fromhex(row.pop("message_hex") or "")
        if raw.startswith(b"\x28\xb5\x2f\xfd"):
            # 解码失败时阻止水位推进，不把乱码当作已处理正文。
            zstandard.ZstdDecompressor().decompress(raw)
        content = message.decode_message_content(raw)
        kind = int(row["local_type"]) & 0xFFFF
        if kind == 49:
            content = appmsg.format_app_message(
                appmsg.parse_app_message(content, row["local_type"])
            )
        elif kind in (10000, 10002):
            content = message.parse_system_message(content)["text"]
        return {
            "chat_id": chat,
            "shard": shard,
            "local_id": int(row["local_id"]),
            "server_id": str(row.get("server_id") or "0"),
            "sender": row["sender"],
            "created_at": int(row["create_time"]),
            "type": int(row["local_type"]),
            "content": content,
        }
