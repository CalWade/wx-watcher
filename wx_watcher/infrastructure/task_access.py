"""Open the selected account's durable task repository."""

import sqlite3
from contextlib import contextmanager

from ..config import Config
from ..reply import ReplyConfig, ReplyStore, now
from ..store import Store


class SqliteTaskAccess:
    def __init__(self, state):
        self.state = state

    @contextmanager
    def open(self):
        config = ReplyConfig.load(self.state.home / "config/reply.toml")
        store = ReplyStore(config.state_dir)
        try:
            yield store
        finally:
            store.close()

    def recent(self):
        file = self.state.home / "config/reply.toml"
        if not file.exists():
            return []
        config = ReplyConfig.load(file)
        database = config.state_dir / "tasks.sqlite3"
        if not database.exists():
            return []
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            return [
                ReplyStore._decode(row[0])
                for row in connection.execute(
                    "SELECT body FROM tasks ORDER BY updated_at DESC LIMIT 100"
                )
            ]

    def reset_cursor(self):
        config = ReplyConfig.load(self.state.home / "config/reply.toml")
        reader = Store(Config.load(config.reader_config).state_dir)
        try:
            with self.open() as tasks, tasks.db:
                cursor = reader.event_cursor()
                resets = tasks.get_meta("cursor_resets", [])[-19:]
                resets.append(
                    {"from": tasks.get_meta("cursor"), "to": cursor, "at": now()}
                )
                tasks.set_meta("cursor_resets", resets)
                tasks.set_meta("cursor", cursor)
                tasks.set_meta("last_error", None)
        finally:
            reader.close()
        return {
            "saved": True,
            "message": "已记录缺口并更新游标；重新开始监控后继续接收新消息",
        }

    def has_inflight(self):
        file = self.state.home / "config/reply.toml"
        if not file.exists():
            return False
        with self.open() as tasks:
            return bool(
                tasks.tasks(["queued", "inference", "sending"], 1) or tasks.pending(1)
            )
