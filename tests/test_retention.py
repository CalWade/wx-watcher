import tempfile
import unittest
from pathlib import Path

from wx_watcher.store import CursorExpired, Store


class RetentionTests(unittest.TestCase):
    def test_expired_prefix_returns_explicit_cursor_error_and_keeps_high_watermark(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state")
            for i in (1, 2, 3):
                store.apply(
                    [
                        (
                            "generation",
                            [{"local_id": i, "created_at": i, "content": "synthetic"}],
                        )
                    ],
                    False,
                )
            with store.conn:
                store.conn.execute("UPDATE events SET received_at=10 WHERE event_id<=2")
                store.conn.execute("UPDATE events SET received_at=30 WHERE event_id=3")
                store.conn.execute(
                    "UPDATE messages SET observed_at=10 WHERE local_id<=2"
                )
            store.prune(20)
            with self.assertRaises(CursorExpired):
                store.events(0, 100)
            self.assertEqual(
                [r["event_id"] for r in store.events(2, 100)["events"]], [3]
            )
            self.assertEqual(store.event_cursor(), 3)
            self.assertTrue(store.had_live_events())
            self.assertEqual(store.watermarks()["generation"], 3)
            store.close()

    def test_late_historical_message_does_not_expire_recent_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state")
            store.apply(
                [
                    (
                        "generation",
                        [
                            {
                                "local_id": 1,
                                "created_at": 1,
                                "content": "historical sync",
                            }
                        ],
                    )
                ],
                False,
            )
            store.prune(1)
            self.assertEqual(len(store.events(0, 10)["events"]), 1)
            store.close()
