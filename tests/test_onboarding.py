import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wx_watcher.controller import Controller
from wx_watcher.onboarding import connect_existing, prepare_dsh


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.c = Controller(self.directory / "app")
        self.c.services.shutdown_event.set()
        self.c.require_current_account = Mock()

    def tearDown(self):
        self.c.pool.shutdown(wait=True)
        self.temp.cleanup()

    def test_running_cost_plugin_is_reused_without_package_or_application_changes(self):
        home = self.directory / "dsh"
        package = home / "profiles/desktop/node_modules/dsh-cost-meter/package.json"
        package.parent.mkdir(parents=True)
        package.write_text(json.dumps({"version": "synthetic-existing"}))
        with (
            patch.dict(os.environ, {"DSH_HOME": str(home)}),
            patch("wx_watcher.reply.LocalAPI") as api,
            patch("wx_watcher.onboarding.subprocess.run") as run,
        ):
            api.return_value.request.return_value = {"ready": True}
            result = prepare_dsh(self.c.state)
        self.assertTrue(result["reused"])
        self.assertEqual(result["version"], "synthetic-existing")
        run.assert_not_called()

    def test_model_step_does_not_inspect_wechat_or_feishu_or_call_a_model(self):
        self.c.services.running = Mock(return_value=False)
        self.c.accounts = Mock(side_effect=AssertionError("unrelated account step"))
        self.c.feishu = Mock(side_effect=AssertionError("unrelated destination step"))
        self.c.install_dsh = Mock(return_value={"ready": True})
        with patch(
            "wx_watcher.onboarding.prepare_dsh", return_value={"installed": True}
        ):
            result = self.c.perform("connect-dsh", {})
        self.assertTrue(result["ready"])
        self.assertTrue(all(c["ok"] for c in result["checks"]))
        self.c.accounts.assert_not_called()
        self.c.feishu.assert_not_called()

    def test_changed_feishu_transport_cannot_keep_the_old_destination(self):
        self.c.services.running = Mock(return_value=False)
        self.c.state.update(
            feishu_transport="cli",
            feishu_chat_id="old-group",
            feishu_chat_name="old",
            send_enabled=True,
        )
        self.c.perform("settings", {"feishu_transport": "openapi"})
        saved = self.c.state.load()
        self.assertEqual(saved["feishu_chat_id"], "")
        self.assertFalse(saved["send_enabled"])

    def test_denied_wechat_access_does_not_block_independent_connections(self):
        self.c.accounts = Mock(side_effect=PermissionError("缺少微信读取权限"))
        self.c.feishu = Mock()
        self.c.feishu.return_value.chats.return_value = {
            "data": {"chats": [{"name": "测试群", "chat_id": "oc_fixture"}]}
        }
        self.c.install_dsh = Mock(return_value={"ready": True})
        with patch(
            "wx_watcher.onboarding.prepare_dsh", return_value={"installed": True}
        ):
            result = connect_existing(self.c)
        self.assertFalse(result["checks"][0]["ok"])
        self.assertEqual(len(result["feishu_chats"]), 1)
        self.assertTrue(result["dsh"]["ready"])

    def test_failed_model_isolation_stops_before_feishu_or_service_start(self):
        def perform(operation, data):
            if operation == "test-dsh":
                return {"tools": 1, "cost_meter": {"found": True}}
            return {"ok": True, "all_verified": True}

        with patch.object(self.c, "perform", side_effect=perform) as call:
            with self.assertRaises(RuntimeError):
                self.c._perform("finish-setup", {})
        self.assertNotIn("test-feishu", [v.args[0] for v in call.call_args_list])
        self.assertNotIn("start", [v.args[0] for v in call.call_args_list])

    def test_missing_cost_record_stops_before_delivery(self):
        def perform(operation, data):
            return (
                {"tools": 0, "cost_meter": {"found": False}}
                if operation == "test-dsh"
                else {"all_verified": True}
            )

        with patch.object(self.c, "perform", side_effect=perform) as call:
            with self.assertRaises(RuntimeError):
                self.c._perform("finish-setup", {})
        self.assertNotIn("test-feishu", [v.args[0] for v in call.call_args_list])

    def test_full_check_runs_in_order_and_starts_only_after_readback(self):
        def perform(operation, data):
            return {
                "tools": 0,
                "cost_meter": {"found": True},
                "readback": True,
                "running": True,
                "all_verified": True,
            }

        with patch.object(self.c, "perform", side_effect=perform) as call:
            result = self.c._perform("finish-setup", {})
        self.assertEqual(
            [v.args[0] for v in call.call_args_list],
            ["run-settings", "verify-key", "test-dsh", "test-feishu", "start"],
        )
        self.assertTrue(result["running"])
