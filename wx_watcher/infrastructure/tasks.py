"""SQLite persistence implements the task repository port."""

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..source import private_dir


def now():
    return datetime.now(UTC).isoformat()


class ReplyStore:
    def __init__(self, directory: Path):
        private_dir(directory)
        self.directory = directory
        file = directory / "tasks.sqlite3"
        self.db = sqlite3.connect(file)
        self.db.row_factory = sqlite3.Row
        os.chmod(file, 0o600)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, event_id INTEGER, status TEXT NOT NULL,
                body TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_events (
                id TEXT PRIMARY KEY, event_id INTEGER NOT NULL, received REAL NOT NULL, body TEXT NOT NULL
            );
        """)

    def get_meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value))
        )

    def bind_scope(self, scope):
        old = self.get_meta("scope")
        if old is not None and old != scope:
            raise ValueError(
                "Reader account/chat changed; use a separate agent state_dir"
            )
        with self.db:
            self.set_meta("scope", scope)

    def save(self, task: dict):
        with self.db:
            self._put(task)

    def _put(self, task):
        self.db.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) "
            "DO UPDATE SET status=excluded.status, body=excluded.body, updated_at=excluded.updated_at",
            (
                task["id"],
                task.get("event_id"),
                task["status"],
                json.dumps(task, ensure_ascii=False),
                now(),
            ),
        )

    def task(self, id):
        row = self.db.execute("SELECT body FROM tasks WHERE id=?", (id,)).fetchone()
        return self._decode(row[0]) if row else None

    def tasks(self, statuses=None, limit=100):
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.db.execute(
                f"SELECT body FROM tasks WHERE status IN ({placeholders}) "
                "ORDER BY event_id, updated_at LIMIT ?",
                (*statuses, limit),
            )
        else:
            rows = self.db.execute(
                "SELECT body FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [self._decode(row[0]) for row in rows]

    def accept(self, task: dict, cursor: int):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO tasks VALUES (?, ?, ?, ?, ?)",
                (
                    task["id"],
                    task["event_id"],
                    task["status"],
                    json.dumps(task, ensure_ascii=False),
                    now(),
                ),
            )
            self.set_meta("cursor", cursor)

    def update_metadata(self, values):
        with self.db:
            for key, value in values.items():
                self.set_meta(key, value)

    def save_many(self, tasks):
        with self.db:
            for task in tasks:
                self._put(task)

    def enqueue_pending(self, task, received):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO pending_events VALUES (?, ?, ?, ?)",
                (
                    task["id"],
                    task["event_id"],
                    received,
                    json.dumps(task, ensure_ascii=False),
                ),
            )
            self.set_meta("cursor", task["event_id"])

    def pending(self, limit=10):
        return [
            {
                "id": row["id"],
                "received": row["received"],
                "task": self._decode(row["body"]),
            }
            for row in self.db.execute(
                "SELECT id,received,body FROM pending_events ORDER BY event_id LIMIT ?",
                (limit,),
            )
        ]

    def commit_batch(self, task, ids):
        with self.db:
            self._put(task)
            self.db.executemany(
                "DELETE FROM pending_events WHERE id=?", ((id,) for id in ids)
            )

    def record_inference(self, task, day, count_call):
        with self.db:
            self._put(task)
            if count_call:
                calls = self.get_meta("daily_calls", {})
                self.set_meta("daily_calls", {day: calls.get(day, 0) + 1})

    def complete_tick(self, timestamp, wall_time, retention_days):
        with self.db:
            self.set_meta("last_success", timestamp)
            self.set_meta("last_error", None)
            if retention_days and wall_time - self.get_meta("last_prune", 0) > 3600:
                cutoff = datetime.fromtimestamp(
                    wall_time - retention_days * 86400, UTC
                ).isoformat()
                self.db.execute(
                    "DELETE FROM tasks WHERE status IN ('sent','skipped','dismissed','resolved') AND updated_at<?",
                    (cutoff,),
                )
                self.set_meta("last_prune", wall_time)

    def counts(self):
        return dict(
            self.db.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")
        )

    @staticmethod
    def _decode(body):
        from .agents.legacy import upgrade_task

        return upgrade_task(json.loads(body))

    def close(self):
        self.db.close()
