from __future__ import annotations

import threading

from ..domain.replies import hash_id
from .ports import Clock, EventRepository, MessageSource


class MessageCollector:
    def __init__(
        self, config, store: EventRepository, *, source: MessageSource, clock: Clock
    ):
        self.config = config
        self.store = store
        self.source = source
        self.clock = clock
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status = {
            "state": "starting",
            "mode": "read_only_database",
            "backend": config.backend,
            "group_name": config.group_name,
            "live_verified": store.had_live_events(),
        }
        self._last_prune = 0.0

    def status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def update_status(self, **values):
        with self._status_lock:
            self._status.update(values)

    def poll(self) -> int:
        started = self.clock.monotonic()
        chat = self.source.resolve()
        self.store.bind_scope(str(self.source.root) + "\n" + chat)
        shards = self.source.shards()
        if not shards:
            raise FileNotFoundError("没有发现 message_N.db 分片")
        baseline = not self.store.initialized()
        marks = self.store.watermarks()
        batches = []
        for shard in shards:
            # 在同一快照内确定代次与水位，避免换库期间串用旧水位。
            after = None if baseline else marks
            generation, rows = self.source.read_shard(
                shard, after, limit=200 if baseline else 1000
            )
            batches.append((generation, rows))
        inserted = self.store.apply(batches, baseline)
        if (
            self.config.retention_days
            and self.clock.monotonic() - self._last_prune > 3600
        ):
            self.store.prune(
                int(self.clock.time()) - self.config.retention_days * 86400
            )
            self._last_prune = self.clock.monotonic()
        self.update_status(
            state="ready",
            error=None,
            chat_id=chat,
            shard_count=len(shards),
            scope_id=hash_id(str(self.source.root) + "\n" + chat),
            last_success=self.clock.timestamp(),
            last_scan_seconds=round(self.clock.monotonic() - started, 4),
            last_new_events=inserted,
            baseline_complete=True,
            group_name=self.source.display_name or self.config.group_name,
            live_verified=self.status()["live_verified"] or inserted > 0,
            event_cursor=self.store.event_cursor(),
        )
        return inserted

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run, name="wechat-db-reader", daemon=True
        )
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:
                self.update_status(
                    state="blocked", error_type=type(exc).__name__, error=str(exc)
                )
            self.stop_event.wait(self.config.poll_interval)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)
