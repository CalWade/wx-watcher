import unittest
from pathlib import Path
from types import SimpleNamespace

from wx_watcher.application.collection import MessageCollector

from .fakes import ManualClock


class MemorySource:
    root = Path("/synthetic/account/db_storage")
    display_name = "合成会话"

    def __init__(self):
        self.paths = [Path("message_0.db")]
        self.reads = []
        self.fail_on = None

    def resolve(self):
        return "synthetic@chatroom"

    def shards(self):
        return self.paths

    def read_shard(self, path, after, limit=1000):
        self.reads.append((path, after, limit))
        if path == self.fail_on:
            raise RuntimeError("synthetic unavailable shard")
        return path.name, [{"local_id": 1}]


class MemoryEvents:
    def __init__(self):
        self.ready = False
        self.commits = []
        self.pruned = []

    def had_live_events(self):
        return False

    def bind_scope(self, scope):
        self.scope = scope

    def initialized(self):
        return self.ready

    def watermarks(self):
        return {"generation": 1}

    def apply(self, batches, baseline):
        self.commits.append((batches, baseline))
        self.ready = True
        return 0 if baseline else 1

    def prune(self, cutoff):
        self.pruned.append(cutoff)

    def event_cursor(self):
        return len(self.commits) - 1


class MessageCollectorUnitTests(unittest.TestCase):
    def setUp(self):
        self.source = MemorySource()
        self.store = MemoryEvents()
        self.clock = ManualClock()
        self.collector = MessageCollector(
            SimpleNamespace(backend="sqlcipher", group_name="", retention_days=7),
            self.store,
            source=self.source,
            clock=self.clock,
        )

    def test_baseline_and_live_reads_use_distinct_limits_and_atomic_commits(self):
        self.assertEqual(self.collector.poll(), 0)
        self.assertEqual(self.source.reads[0][1:], (None, 200))
        self.assertFalse(self.collector.status()["live_verified"])
        self.assertEqual(self.collector.poll(), 1)
        self.assertEqual(self.source.reads[1][1:], ({"generation": 1}, 1000))
        status = self.collector.status()
        self.assertTrue(status["live_verified"])
        self.assertEqual(status["group_name"], "合成会话")
        self.assertEqual(status["event_cursor"], 1)
        status["state"] = "changed-copy"
        self.assertEqual(self.collector.status()["state"], "ready")

    def test_shard_failure_cannot_commit_a_partial_scan_or_advance_baseline(self):
        other = Path("message_1.db")
        self.source.paths.append(other)
        self.source.fail_on = other
        with self.assertRaises(RuntimeError):
            self.collector.poll()
        self.assertFalse(self.store.ready)
        self.assertEqual(self.store.commits, [])
        self.source.fail_on = None
        self.collector.poll()
        self.assertEqual(len(self.store.commits[0][0]), 2)
        self.assertTrue(self.store.commits[0][1])

    def test_missing_shards_and_retention_use_explicit_clock(self):
        self.source.paths = []
        with self.assertRaises(FileNotFoundError):
            self.collector.poll()
        self.source.paths = [Path("message_0.db")]
        self.collector.poll()
        self.collector.poll()
        self.assertEqual(self.store.pruned, [int(self.clock.time()) - 7 * 86400])
        self.clock.sleep(3601)
        self.collector.poll()
        self.assertEqual(len(self.store.pruned), 2)
