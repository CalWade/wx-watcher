"""Real processes, encrypted WAL, API, worker and two local HTTP peers."""

import unittest

from scripts.dev import acceptance


class FullApplicationIntegrationTests(unittest.TestCase):
    def test_fresh_environment_delivers_a_new_event_and_restarts_without_duplicates(
        self,
    ):
        result = acceptance()
        self.assertTrue(result["passed"])
        self.assertTrue(result["encrypted_source"])
        self.assertTrue(result["new_message_delivered"])
        self.assertTrue(result["restart_idempotent"])
        self.assertEqual(result["provider"], "http")


class ProductionProcessIntegrationTests(unittest.TestCase):
    def test_real_reader_and_worker_processes_resume_a_bound_agent_task(self):
        import tempfile
        import time
        from pathlib import Path

        from support.sandbox import Sandbox
        from test_pipeline import CHAT, GROUP

        from wx_watcher.services import Services

        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Sandbox(Path(tmp))
            services = Services(sandbox.state, account_guard=lambda: None)
            try:
                sandbox.state.update(chat_id=CHAT, group_name=GROUP, send_enabled=False)
                self.assertTrue(services.start()["running"])
                sandbox.emit("真实子进程的合成问题")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    tasks = sandbox.controller.tasks.recent()
                    if tasks and tasks[0]["status"] == "draft":
                        break
                    time.sleep(0.2)
                else:
                    self.fail("真实读取/回复子进程未生成草稿")
                self.assertEqual(tasks[0]["agent"]["provider_id"], "http")
                count = sandbox.peers.values["submissions"]
                self.assertFalse(services.stop()["running"])
                self.assertTrue(services.start()["running"])
                time.sleep(1)
                self.assertEqual(sandbox.peers.values["submissions"], count)
                self.assertEqual(len(sandbox.controller.tasks.recent()), 1)
            finally:
                services.stop()
                services.shutdown_event.set()
                sandbox.close()
