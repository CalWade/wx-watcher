#!/usr/bin/env python3
"""真实 SQLCipher 加密、合成消息与 32 MiB WAL 的端到端本机计时。"""
import json
from pathlib import Path
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / "tests")]
from test_pipeline import Fixture
from wx_watcher.collector import Collector
from wx_watcher.store import Store

with tempfile.TemporaryDirectory() as temp:
    fixture = Fixture(Path(temp), encrypted=True)
    store = Store(fixture.config.state_dir)
    collector = Collector(fixture.config, store)
    try:
        collector.poll()
        fixture.shard.exec("CREATE TABLE filler(data BLOB);"
                           "INSERT INTO filler VALUES(randomblob(33554432));")
        started = time.monotonic()
        fixture.insert(fixture.shard, 2, "合成消息：WAL 延迟测试")
        collector.poll()
        events = store.events(0, 10)["events"]
        assert len(events) == 1 and events[0]["content"] == "合成消息：WAL 延迟测试"
        report = {
            "scope": "Synthetic message, real SQLCipher4 encryption, 32 MiB filler WAL",
            "wal_bytes": (fixture.root / "message/message_0.db-wal").stat().st_size,
            "commit_to_event_seconds": round(time.monotonic() - started, 3),
            "live_wechat_latency_verified": False,
        }
        (root / "output/benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        collector.stop()
        store.close()
        fixture.close()
