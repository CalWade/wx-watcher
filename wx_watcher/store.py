from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .source import private_dir


class CursorExpired(ValueError):
    pass


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
        for table, column in (("events", "received_at"), ("messages", "observed_at")):
            columns = {
                r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                with self.conn:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} REAL NOT NULL DEFAULT 0"
                    )
                    self.conn.execute(f"UPDATE {table} SET {column}=?", (time.time(),))

    def initialized(self) -> bool:
        with self.lock:
            return (
                self.conn.execute(
                    "SELECT 1 FROM meta WHERE key='initialized'"
                ).fetchone()
                is not None
            )

    def bind_scope(self, scope: str):
        with self.lock, self.conn:
            current = self.conn.execute(
                "SELECT value FROM meta WHERE key='scope'"
            ).fetchone()
            if current and current["value"] != scope:
                raise ValueError(
                    "账号或目标群已改变，请使用独立 state_dir，防止混用历史消息"
                )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta VALUES ('scope', ?)", (scope,)
            )

    def watermarks(self) -> dict[str, int]:
        with self.lock:
            return {
                r["source"]: r["last_id"]
                for r in self.conn.execute("SELECT * FROM sources")
            }

    def apply(self, batches: list[tuple[str, list[dict]]], baseline: bool) -> int:
        inserted = 0
        with self.lock, self.conn:
            for source, rows in batches:
                for msg in sorted(rows, key=lambda m: m["local_id"]):
                    uid = f"{source}:{msg['local_id']}"
                    body = json.dumps({**msg, "message_id": uid}, ensure_ascii=False)
                    self.conn.execute(
                        "INSERT INTO messages(uid,created_at,local_id,body,observed_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(uid) "
                        "DO UPDATE SET body=excluded.body, created_at=excluded.created_at",
                        (uid, msg["created_at"], msg["local_id"], body, time.time()),
                    )
                    if not baseline:
                        cursor = self.conn.execute(
                            "INSERT OR IGNORE INTO events(uid, body, received_at) VALUES (?, ?, ?)",
                            (uid, body, time.time()),
                        )
                        inserted += cursor.rowcount
                last_id = max((r["local_id"] for r in rows), default=0)
                self.conn.execute(
                    "INSERT INTO sources VALUES (?, ?) ON CONFLICT(source) "
                    "DO UPDATE SET last_id=max(last_id, excluded.last_id)",
                    (source, last_id),
                )
            self.conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('initialized','true')"
            )
        return inserted

    def history(self, limit: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT body FROM messages ORDER BY created_at DESC, local_id DESC, uid DESC LIMIT ?",
                (limit,),
            )
            return list(reversed([json.loads(r["body"]) for r in rows]))

    def events(self, after: int, limit: int) -> dict:
        with self.lock:
            floor = self.conn.execute(
                "SELECT value FROM meta WHERE key='event_floor'"
            ).fetchone()
            if floor and after < int(floor[0]):
                raise CursorExpired(
                    "事件游标早于数据保留范围，请核对后重新建立消费基线"
                )
            rows = list(
                self.conn.execute(
                    "SELECT event_id, body FROM events WHERE event_id>? ORDER BY event_id LIMIT ?",
                    (after, limit),
                )
            )
        return {
            "events": [
                {"event_id": r["event_id"], **json.loads(r["body"])} for r in rows
            ],
            "next_cursor": rows[-1]["event_id"] if rows else after,
        }

    def event_cursor(self):
        with self.lock:
            row = self.conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='events'"
            ).fetchone()
            return int(row[0]) if row else 0

    def had_live_events(self):
        return self.event_cursor() > 0

    def prune(self, before_timestamp: int):
        with self.lock, self.conn:
            boundary = self.conn.execute(
                "SELECT MIN(event_id) FROM events WHERE received_at>=?",
                (before_timestamp,),
            ).fetchone()[0]
            floor = self.conn.execute(
                "SELECT MAX(event_id) FROM events WHERE received_at<? AND event_id<?",
                (before_timestamp, boundary if boundary is not None else 2**63 - 1),
            ).fetchone()[0]
            if floor is not None:
                # Preserve a contiguous expiration boundary, never create silent holes for consumers.
                self.conn.execute("DELETE FROM events WHERE event_id<=?", (floor,))
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('event_floor',?)",
                    (str(floor),),
                )
            return self.conn.execute(
                "DELETE FROM messages WHERE observed_at<? AND uid NOT IN (SELECT uid FROM events)",
                (before_timestamp,),
            ).rowcount

    def close(self):
        with self.lock:
            self.conn.close()
