import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wx_watcher.appstate import atomic_json
from wx_watcher.controller import Controller
from wx_watcher.keyflow import KeyFlow
from wx_watcher.reply import ReplyConfig, ReplyStore


class TaskReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.c = Controller(self.root / "app")
        self.c.services.shutdown_event.set()
        root = self.root / "db_storage"
        (root / "contact").mkdir(parents=True)
        (root / "contact/contact.db").touch()
        self.c.state.add_profile(root, "synthetic-reference")
        self.c.state.update(chat_id="filehelper", group_name="合成会话")
        _, reply = self.c.state.write_configs()
        self.store = ReplyStore(ReplyConfig.load(reply).state_dir)

    def tearDown(self):
        self.store.db.close()
        self.c.pool.shutdown(wait=True)
        self.temp.cleanup()

    def task(self, status):
        self.store.save(
            {
                "id": "test-task",
                "status": status,
                "prompt": "synthetic question",
                "event_id": 1,
            }
        )

    def test_uncertain_delivery_requires_explicit_review(self):
        self.task("delivery_review")
        with self.assertRaises(ValueError):
            self.c.perform("task-review", {"id": "test-task", "decision": "retry"})
        self.assertEqual(self.store.task("test-task")["status"], "delivery_review")
        self.c.perform(
            "task-review",
            {"id": "test-task", "decision": "retry", "confirmed_not_delivered": True},
        )
        self.assertEqual(self.store.task("test-task")["status"], "draft")

    def test_model_retry_creates_fresh_id_and_preserves_original(self):
        self.task("needs_review")
        self.c.perform("task-review", {"id": "test-task", "decision": "retry"})
        original = self.store.task("test-task")
        retry = self.store.task(original["replacement_id"])
        self.assertEqual(original["status"], "resolved")
        self.assertEqual(retry["status"], "queued")
        self.assertEqual(retry["retry_of"], "test-task")
        self.assertEqual(retry["prompt"], "synthetic question")

    def test_review_rejected_while_worker_is_active(self):
        self.task("draft")
        with (
            patch.object(self.c.services, "running", return_value=True),
            self.assertRaises(ValueError),
        ):
            self.c.perform("task-review", {"id": "test-task", "decision": "dismiss"})
        self.assertEqual(self.store.task("test-task")["status"], "draft")

    def test_failed_preflight_never_launches_privileged_job(self):
        flow = KeyFlow(self.c.state)
        with (
            patch.object(flow, "preflight", return_value={"ready": False}),
            patch("wx_watcher.keyflow.subprocess.Popen") as launch,
        ):
            with self.assertRaises(ValueError):
                flow.start("/example/db_storage")
        launch.assert_not_called()

    def test_orphaned_capture_surfaces_recovery(self):
        flow = KeyFlow(self.c.state)
        flow.jobs = self.root / "jobs"
        job = flow.jobs / "00000000-0000-4000-8000-000000000001"
        (job / "WeChat.original.app").mkdir(parents=True)
        atomic_json(job / "request.json", {"owner_pid": 9999999})
        atomic_json(
            job / "progress.json", {"phase": "attaching", "helper_pid": 9999998}
        )
        self.assertEqual(flow.sessions()[0]["phase"], "recovery_required")
