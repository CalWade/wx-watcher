import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wx_watcher.onboarding import check_first_connection


class FirstConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name) / "db_storage")
        self.controller = Mock()
        self.controller.accounts.return_value = [
            {"label": "synthetic", "db_root": self.root, "active": True}
        ]
        self.native = {
            "wechat_installed": True,
            "official_signature": True,
            "hardened_runtime": True,
            "tested_build": True,
            "tested_os": True,
            "tested_architecture": True,
            "lldb": "/example/lldb",
            "debugger_compatible": True,
            "database_readable": True,
            "data_access_ready": True,
            "data_directory_errno": 0,
        }

    def tearDown(self):
        self.temp.cleanup()

    def check(self, root="", discover=False):
        with patch(
            "wx_watcher.credentials.native_request", return_value=self.native
        ) as native:
            result = check_first_connection(self.controller, root, discover)
        # This operation must not request application-write access or a privileged job.
        self.assertEqual(native.call_args.args[0]["operation"], "connection_preflight")
        return result

    def test_current_account_is_selected_without_authorization_or_capture(self):
        result = self.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["db_root"], self.root)
        self.controller.services.start.assert_not_called()

    def test_empty_machine_can_prepare_listener_before_first_login(self):
        self.controller.accounts.return_value = []
        self.native.update(database_readable=False, data_directory_errno=errno.ENOENT)
        result = self.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["account_mode"], "login")
        self.assertEqual(result["db_root"], "")
        checks = {c["id"]: c for c in result["checks"]}
        self.assertTrue(checks["access"]["ok"])
        self.assertIn("登录后自动", checks["database"]["message"])

    def test_native_privacy_denial_blocks_even_if_parent_can_list_accounts(self):
        self.native.update(database_readable=False, database_access_errno=errno.EPERM)
        result = self.check()
        self.assertFalse(result["ready"])
        self.assertFalse(next(c for c in result["checks"] if c["id"] == "access")["ok"])

    def test_missing_debugger_or_unsupported_build_blocks_before_capture(self):
        for field in [
            "lldb",
            "tested_build",
            "tested_os",
            "tested_architecture",
            "debugger_compatible",
        ]:
            with self.subTest(field=field):
                saved = self.native[field]
                self.native[field] = False
                self.assertFalse(self.check()["ready"])
                self.native[field] = saved

    def test_multiple_historical_accounts_are_not_guessed(self):
        self.controller.accounts.return_value = [
            {"label": "old-a", "db_root": self.root, "active": False},
            {"label": "old-b", "db_root": self.root + "-other", "active": False},
        ]
        result = self.check()
        self.assertFalse(result["ready"])
        self.assertEqual(result["db_root"], "")

    def test_explicit_account_can_be_selected_before_switching_login(self):
        result = self.check(self.root + "-other")
        self.assertTrue(result["ready"])
        self.assertEqual(result["db_root"], str(Path(self.root + "-other").resolve()))

    def test_missing_directory_does_not_imply_permission(self):
        self.controller.accounts.return_value = []
        self.native.update(
            database_readable=False,
            data_access_ready=False,
            data_directory_errno=errno.ENOENT,
        )
        result = self.check()
        self.assertFalse(result["ready"])
        checks = {c["id"]: c for c in result["checks"]}
        self.assertIn("权限尚未确认", checks["database"]["message"])
        self.assertFalse(checks["access"]["ok"])

    def test_login_other_account_does_not_reuse_active_historical_root(self):
        result = self.check(discover=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["account_mode"], "login")
        self.assertEqual(result["db_root"], "")
