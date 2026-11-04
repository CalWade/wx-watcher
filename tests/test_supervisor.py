import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wx_watcher.appstate import AppState
from wx_watcher.services import Services


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.services = Services(AppState(Path(self.temp.name) / "app"))
        self.services.shutdown_event.set()

    def tearDown(self):
        self.temp.cleanup()

    def test_restarts_are_bounded_and_owned_services_can_be_stopped(self):
        child = Mock()
        child.poll.return_value = 1
        self.services.children["reader"] = child
        self.services.expected_running = True
        with patch.object(self.services, "_spawn") as spawn:
            for _ in range(4):
                self.services._watch_once()
        self.assertEqual(spawn.call_count, 3)
        self.assertFalse(self.services.expected_running)
        self.assertIn("重复退出", self.services.last_error)
        self.services.stop()
        child.terminate.assert_not_called()

    def test_start_failure_cleans_up_child_and_reports_failure(self):
        with (
            patch.object(
                self.services, "_start", side_effect=RuntimeError("读取服务启动失败")
            ),
            patch.object(self.services, "stop") as stop,
        ):
            with self.assertRaises(RuntimeError):
                self.services.start()
        stop.assert_called_once()
        self.assertEqual(self.services.last_error, "读取服务启动失败")

    def test_log_rotation_keeps_bounded_private_tail(self):
        file = self.services.state.home / "reader.log"
        file.write_bytes(b"x" * 2_100_000)
        self.services._watch_once()
        self.assertEqual(file.stat().st_size, 0)
        previous = file.with_suffix(".previous.log")
        self.assertEqual(previous.stat().st_size, 1_000_000)
        self.assertEqual(previous.stat().st_mode & 0o777, 0o600)
