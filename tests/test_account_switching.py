import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wx_watcher.controller import Controller
from wx_watcher.onboarding import connect_existing


class AccountSwitchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        self.c = Controller(self.home / "app")
        self.c.services.shutdown_event.set()
        self.old = self.home / "old/db_storage"
        self.current = self.home / "current/db_storage"
        for root in [self.old, self.current]:
            (root / "contact").mkdir(parents=True)
            (root / "contact/contact.db").write_bytes(b"synthetic")
        self.c.state.add_profile(self.old, "synthetic-old-reference", "旧账号")
        self.old_profile = self.c.state.profile()
        self.c.state.update(
            chat_id="old-room",
            group_name="旧会话",
            send_enabled=True,
            autostart_monitor=True,
            feishu_chat_id="oc_internal",
        )
        self.c.accounts = Mock(
            return_value=[
                {"label": "旧账号", "db_root": str(self.old), "active": False},
                {"label": "当前账号", "db_root": str(self.current), "active": True},
            ]
        )

    def tearDown(self):
        self.c.pool.shutdown(wait=True)
        self.temp.cleanup()

    def test_status_distinguishes_current_login_from_selected_saved_credential(self):
        result = self.c.account_status()
        self.assertEqual(result["code"], "needs_credentials")
        self.assertEqual(result["current"]["label"], "当前账号")
        self.assertFalse(result["current"]["credential_saved"])
        self.assertEqual(result["selected"]["label"], "旧账号")
        self.assertNotIn("synthetic-old-reference", json.dumps(result))
        self.assertEqual(self.c.state.profile(), self.old_profile)

    def test_unconfirmed_login_is_never_inferred_from_one_saved_profile(self):
        self.c.accounts.return_value = []
        result = self.c.account_status()
        self.assertEqual(result["code"], "unconfirmed")
        self.assertIsNone(result["current"])
        self.assertFalse(result["matches_current"])
        self.assertFalse(result["can_configure"])

    def test_saved_local_account_can_be_configured_without_claiming_login(self):
        self.c.accounts.return_value = [
            {"label": "旧账号", "db_root": str(self.old), "active": False}
        ]
        result = self.c.account_status()
        self.assertEqual(result["code"], "local_ready")
        self.assertTrue(result["can_configure"])
        self.assertIsNone(result["current"])
        self.assertFalse(result["matches_current"])

    def test_ambiguous_or_different_login_cannot_configure_selected_account(self):
        self.assertFalse(self.c.account_status()["can_configure"])
        self.c.accounts.return_value[0]["active"] = True
        self.assertFalse(self.c.account_status()["can_configure"])
        with patch(
            "wx_watcher.discovery.active_roots", return_value=[self.old, self.current]
        ):
            with self.assertRaisesRegex(ValueError, "多个登录账号"):
                self.c.conversations()

    def test_local_conversations_and_selection_work_but_monitoring_stays_blocked(self):
        reader = Mock()
        reader.config.db_root = self.old
        reader.rows.return_value = [
            {"username": "filehelper", "nick_name": "文件传输助手"}
        ]
        with (
            patch("wx_watcher.discovery.active_roots", return_value=[]),
            patch.object(self.c, "reader", return_value=reader),
            patch.object(self.c.services, "start") as start,
            patch.object(self.c, "dsh") as model,
            patch.object(self.c, "feishu") as delivery,
        ):
            rows = self.c.perform("conversations", {})
            self.assertEqual(rows[0]["username"], "filehelper")
            self.c.perform(
                "settings", {"chat_id": "filehelper", "group_name": "文件传输助手"}
            )
            for op in ["start", "finish-setup"]:
                with (
                    self.subTest(operation=op),
                    self.assertRaisesRegex(ValueError, "尚未确认"),
                ):
                    self.c.perform(op, {})
        reader.rows.assert_called_once()
        self.assertEqual(self.c.state.load()["chat_id"], "filehelper")
        start.assert_not_called()
        model.assert_not_called()
        delivery.assert_not_called()

    def test_account_change_blocks_stale_conversation_selection(self):
        before = self.c.state.load()
        with patch("wx_watcher.discovery.active_roots", return_value=[self.current]):
            with self.assertRaisesRegex(ValueError, "账号不同"):
                self.c.perform(
                    "settings", {"chat_id": "filehelper", "group_name": "文件传输助手"}
                )
        self.assertEqual(self.c.state.load(), before)

    def test_permission_denial_keeps_saved_accounts_visible(self):
        self.c.accounts.side_effect = PermissionError("synthetic")
        result = self.c.account_status()
        self.assertEqual(result["code"], "unavailable")
        self.assertFalse(result["can_configure"])
        self.assertEqual(result["selected"]["label"], "旧账号")
        self.assertTrue(result["accounts"][0]["credential_saved"])

    def test_selecting_historical_profile_is_blocked_before_keychain_access(self):
        before = self.c.state.load()
        with (
            patch("wx_watcher.discovery.active_roots", return_value=[self.current]),
            patch("wx_watcher.controller.native_request") as native,
        ):
            with self.assertRaisesRegex(ValueError, "账号不同"):
                self.c.perform("settings", {"active_profile": self.old_profile["id"]})
        native.assert_not_called()
        self.assertEqual(self.c.state.load(), before)

    def test_start_and_full_setup_refuse_mismatch_before_external_actions(self):
        with (
            patch("wx_watcher.discovery.active_roots", return_value=[self.current]),
            patch.object(self.c.services, "start") as start,
            patch.object(self.c, "install_dsh") as bridge,
            patch.object(self.c, "feishu") as feishu,
        ):
            for op in ["start", "finish-setup", "conversations"]:
                with (
                    self.subTest(operation=op),
                    self.assertRaisesRegex(ValueError, "账号不同"),
                ):
                    self.c.perform(op, {})
        start.assert_not_called()
        bridge.assert_not_called()
        feishu.assert_not_called()

    def test_switch_validates_saved_key_then_clears_cross_account_run_options(self):
        self.c.state.add_profile(
            self.current, "synthetic-current-reference", "当前账号"
        )
        new_profile = self.c.state.profile()
        self.c.state.update(
            active_profile=self.old_profile["id"],
            chat_id="old-room",
            group_name="旧会话",
            send_enabled=True,
            autostart_monitor=True,
        )
        with (
            patch("wx_watcher.discovery.active_roots", return_value=[self.current]),
            patch(
                "wx_watcher.controller.native_request",
                return_value={"all_verified": True},
            ) as verify,
        ):
            self.c.perform("settings", {"active_profile": new_profile["id"]})
        verify.assert_called_once_with(
            {
                "operation": "verify",
                "db_root": str(self.current),
                "key_ref": "synthetic-current-reference",
            }
        )
        saved = self.c.state.load()
        self.assertEqual(saved["active_profile"], new_profile["id"])
        self.assertEqual(saved["chat_id"], "")
        self.assertFalse(saved["send_enabled"])
        self.assertFalse(saved["autostart_monitor"])
        self.assertEqual(saved["feishu_chat_id"], "oc_internal")
        self.assertIn(self.old_profile, saved["profiles"])

    def test_failed_key_validation_leaves_all_configuration_intact(self):
        self.c.state.add_profile(
            self.current, "synthetic-current-reference", "当前账号"
        )
        new_profile = self.c.state.profile()
        self.c.state.update(active_profile=self.old_profile["id"], chat_id="old-room")
        before = self.c.state.load()
        with (
            patch("wx_watcher.discovery.active_roots", return_value=[self.current]),
            patch(
                "wx_watcher.controller.native_request",
                return_value={"all_verified": False},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "未通过数据库验证"):
                self.c.perform("settings", {"active_profile": new_profile["id"]})
        self.assertEqual(before, self.c.state.load())

    def test_auto_connect_does_not_verify_or_read_the_old_account(self):
        with (
            patch.object(self.c, "agent") as agent,
            patch.object(self.c, "feishu") as feishu,
            patch.object(self.c, "conversations") as conversations,
            patch("wx_watcher.controller.native_request") as native,
        ):
            agent.return_value.prepare.return_value = {"ready": False}
            feishu.return_value.chats.return_value = {"data": {"chats": []}}
            result = connect_existing(self.c)
        native.assert_not_called()
        conversations.assert_not_called()
        self.assertTrue(
            any("不会改用" in c.get("message", "") for c in result["checks"])
        )

    def test_running_services_pause_when_current_account_changes(self):
        worker, reader = Mock(), Mock()
        for child in [worker, reader]:
            child.poll.return_value = None
        self.c.services.children.update(worker=worker, reader=reader)
        self.c.services.expected_running = True
        with patch("wx_watcher.discovery.active_roots", return_value=[self.current]):
            self.c.services._watch_once()
        worker.terminate.assert_called_once()
        reader.terminate.assert_called_once()
        self.assertFalse(self.c.services.expected_running)
        self.assertIn("监控已暂停", self.c.services.last_error)
        self.assertEqual(self.c.services.children, {})

    def test_running_services_pause_when_login_can_no_longer_be_confirmed(self):
        self.c.services.expected_running = True
        with patch("wx_watcher.discovery.active_roots", return_value=[]):
            self.c.services._watch_once()
        self.assertFalse(self.c.services.expected_running)
        self.assertIn("尚未确认", self.c.services.last_error)
