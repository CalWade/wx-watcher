from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading

from .source import private_dir


class Store:
    """自有事件库；水位与事件在同一事务落盘，客户端用 event_id 断点续取。"""
    def __init__(self, directory: Path):
        private_dir(directory)
        path = directory / "events.sqlite3"
        self.conn = sqlite3.connect(path, check_same_thread=False)
        os.chmod(path, 0o600)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
                source TEXT PRIMARY KEY, last_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages(
                uid TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
                local_id INTEGER NOT NULL, body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT UNIQUE NOT NULL, body TEXT NOT NULL
            );
        """)

    def initialized(self) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone() is not None

    def bind_scope(self, scope: str):
        with self.lock, self.conn:
            current = self.conn.execute("SELECT value FROM meta WHERE key='scope'").fetchone()
            if current and current["value"] != scope:
                raise ValueError("账号或目标群已改变，请使用独立 state_dir，防止混用历史消息")
            self.conn.execute("INSERT OR IGNORE INTO meta VALUES ('scope', ?)", (scope,))

    def watermarks(self) -> dict[str, int]:
        with self.lock:
            return {r["source"]: r["last_id"] for r in self.conn.execute("SELECT * FROM sources")}

    def apply(self, batches: list[tuple[str, list[dict]]], baseline: bool) -> int:
        inserted = 0
        with self.lock, self.conn:
            for source, rows in batches:
                for msg in sorted(rows, key=lambda m: m["local_id"]):
                    uid = f'{source}:{msg["local_id"]}'
                    body = json.dumps({**msg, "message_id": uid}, ensure_ascii=False)
                    self.conn.execute(
                        "INSERT INTO messages VALUES (?, ?, ?, ?) ON CONFLICT(uid) "
                        "DO UPDATE SET body=excluded.body, created_at=excluded.created_at",
                        (uid, msg["created_at"], msg["local_id"], body),
                    )
                    if not baseline:
                        cursor = self.conn.execute(
                            "INSERT OR IGNORE INTO events(uid, body) VALUES (?, ?)", (uid, body))
                        inserted += cursor.rowcount
                last_id = max((r["local_id"] for r in rows), default=0)
                self.conn.execute(
                    "INSERT INTO sources VALUES (?, ?) ON CONFLICT(source) "
                    "DO UPDATE SET last_id=max(last_id, excluded.last_id)", (source, last_id))
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('initialized','true')")
        return inserted

    def history(self, limit: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT body FROM messages ORDER BY created_at DESC, local_id DESC, uid DESC LIMIT ?",
                (limit,))
            return list(reversed([json.loads(r["body"]) for r in rows]))

    def events(self, after: int, limit: int) -> dict:
        with self.lock:
            rows = list(self.conn.execute(
                "SELECT event_id, body FROM events WHERE event_id>? ORDER BY event_id LIMIT ?",
                (after, limit)))
        return {
            "events": [{"event_id": r["event_id"], **json.loads(r["body"])} for r in rows],
            "next_cursor": rows[-1]["event_id"] if rows else after,
        }

    def close(self):
        with self.lock:
            self.conn.close()
