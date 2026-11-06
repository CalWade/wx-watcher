import unittest
from pathlib import Path

from wx_watcher.application.accounts import AccountsUseCases
from wx_watcher.application.connections import ConnectionsUseCases
from wx_watcher.application.credentials import CredentialsUseCases
from wx_watcher.application.runtime import RuntimeUseCases
from wx_watcher.application.settings import SettingsUseCases
from wx_watcher.application.tasks import TasksUseCases

from .fakes import MemoryContext


class AccountsUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.context = MemoryContext()
        self.service = AccountsUseCases(self.context)

    def test_account_actions_only_use_display_and_catalog_ports(self):
        status = self.service.handle("account-status", {})
        self.assertTrue(status["matches_current"])
        self.assertTrue(status["can_configure"])
        self.assertEqual(len(self.service.handle("accounts", {})), 1)
        root = self.context.state.profile()["db_root"]
        self.service.handle("account-name", {"db_root": root, "label": "工作号"})
        self.assertEqual(self.context.credential_calls, [])
        self.assertEqual(
            self.service.handle("refresh-account-name", {"db_root": root})["nickname"],
            "合成昵称",
        )
        self.assertEqual(
            self.service.handle("conversations", {"query": "助手"})[0]["username"],
            "filehelper",
        )
        with self.assertRaises(ValueError):
            self.service.handle("account-name", {"db_root": "/other"})

    def test_absent_login_allows_configuration_and_different_login_requires_switch(
        self,
    ):
        self.context.active = []
        self.context.discovered[0]["active"] = False
        self.assertEqual(self.service.account_status()["code"], "local_ready")
        self.assertTrue(self.service.account_status()["can_configure"])
        self.service.conversations()
        with self.assertRaises(ValueError):
            self.service.require_current_account()
        self.context.active = [Path("/synthetic/other/db_storage")]
        self.context.discovered.append(
            {"db_root": str(self.context.active[0]), "label": "other", "active": True}
        )
        self.assertEqual(self.service.account_status()["code"], "needs_credentials")
        with self.assertRaises(ValueError):
            self.service.conversations()

    def test_permission_or_ambiguous_identity_preserves_saved_credential(self):
        self.context.discovered = PermissionError("denied")
        status = self.service.account_status()
        self.assertEqual(status["code"], "unavailable")
        self.assertIsNotNone(status["selected"])
        self.assertFalse(status["can_configure"])
        self.context.discovered = []
        self.assertEqual(self.service.account_status()["code"], "unconfirmed")

    def test_saved_other_login_requires_explicit_verified_switch(self):
        self.context.state.add_profile("/synthetic/other/db_storage", "other-ref")
        self.assertEqual(self.service.account_status()["code"], "switch_required")
        self.context.state.update(profiles=[])
        self.assertEqual(self.service.account_status()["code"], "needs_credentials")


class CredentialsUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.context = MemoryContext()
        self.service = CredentialsUseCases(self.context)

    def test_import_verify_backup_restore_use_only_credential_port(self):
        result = self.service.handle(
            "import-key",
            {
                "db_root": "/synthetic/new/db_storage",
                "key_file": "/synthetic/key",
                "label": "新账号",
            },
        )
        self.assertTrue(result["connected"])
        self.assertEqual(
            self.context.state.profile()["key_ref"], "synthetic-new-reference"
        )
        self.assertTrue(self.service.handle("verify-key", {})["all_verified"])
        self.service.handle("backup-key", {"password": "synthetic-test-password"})
        self.service.handle(
            "restore-key",
            {
                "db_root": "/synthetic/new/db_storage",
                "password": "synthetic-test-password",
                "backup": {},
            },
        )
        self.assertEqual(
            [call["operation"] for call in self.context.credential_calls],
            ["store", "verify", "export_backup", "import_backup"],
        )

    def test_running_reader_blocks_credential_changes_before_access(self):
        self.context.services.start()
        for operation, data in [
            ("import-key", {}),
            ("restore-key", {}),
            ("capture-start", {}),
        ]:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.service.handle(operation, data)
        self.assertEqual(self.context.credential_calls, [])
        self.assertEqual(self.context.capture.calls, [])

    def test_capture_actions_delegate_and_preserve_completed_account_binding(self):
        self.assertTrue(
            self.service.handle("capture-preflight", {"discover_account": True})[
                "ready"
            ]
        )
        self.service.handle("capture-start", {"discover_account": True})
        data = {"capture_id": "synthetic-capture"}
        self.assertTrue(self.service.handle("capture-login-ready", data)["ready"])
        self.assertEqual(len(self.service.handle("capture-sessions", {})), 1)
        self.assertTrue(self.service.handle("capture-cancel", data)["cancel_requested"])
        self.assertEqual(
            self.service.handle("capture-recover", data)["phase"], "recovered"
        )
        self.context.capture.phase = "completed"
        result = self.service.handle("capture-status", data)
        self.assertIn("account_label", result)
        self.assertTrue(self.context.metadata_calls)
        self.context.state.values["account_names"][result["db_root"]][
            "identity_profile_id"
        ] = self.context.state.profile()["id"]
        count = len(self.context.metadata_calls)
        self.service.handle("capture-status", data)
        self.assertEqual(len(self.context.metadata_calls), count)


class SettingsUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.context = MemoryContext()
        self.service = SettingsUseCases(self.context)

    def test_settings_are_validated_before_credentials_or_persistence(self):
        before = self.context.state.load()
        with self.assertRaises(ValueError):
            self.service.handle("settings", {"send_enabled": "true"})
        self.assertEqual(self.context.state.load(), before)
        self.assertEqual(self.context.credential_calls, [])
        self.context.services.start()
        with self.assertRaises(ValueError):
            self.service.handle("settings", {})

    def test_verified_switch_clears_cross_account_options(self):
        self.context.state.add_profile("/synthetic/other/db_storage", "other-reference")
        self.context.state.update(
            active_profile="owner",
            chat_id="old",
            send_enabled=True,
            autostart_monitor=True,
        )
        self.context.active = [Path("/synthetic/other/db_storage")]
        self.service.handle(
            "settings", {"active_profile": "/synthetic/other/db_storage"}
        )
        state = self.context.state.load()
        self.assertEqual(state["chat_id"], "")
        self.assertFalse(state["send_enabled"])
        self.assertFalse(state["autostart_monitor"])
        self.assertEqual(state["feishu_chat_id"], "oc_synthetic")

    def test_failed_verification_and_pending_agent_work_cannot_change_selection(self):
        self.context.credential_request = lambda data: {"all_verified": False}
        before = self.context.state.load()
        with self.assertRaises(ValueError):
            self.service.handle("settings", {"active_profile": "owner"})
        self.assertEqual(self.context.state.load(), before)
        self.context.tasks.save({"id": "pending", "status": "inference"})
        with self.assertRaises(ValueError):
            self.service.handle("settings", {"agent_provider": "other-test"})
        self.assertEqual(self.context.state.load(), before)
        with self.assertRaises(ValueError):
            self.service.handle("settings", {"agent_options": {"model": "other"}})
        self.assertEqual(self.context.state.load(), before)
        with self.assertRaises(ValueError):
            self.service.handle("settings", {"agent_provider": "not-installed"})

    def test_delivery_identity_change_clears_destination_and_saves_secret_reference(
        self,
    ):
        self.service.handle(
            "settings",
            {
                "feishu_transport": "openapi",
                "knowledge_files": ["/synthetic/facts.txt"],
            },
        )
        self.assertEqual(self.context.state.load()["feishu_chat_id"], "")
        with self.assertRaises(ValueError):
            self.service.handle("feishu-secret", {"app_id": "invalid"})
        self.service.handle(
            "feishu-secret", {"app_id": "cli_synthetic", "secret": "synthetic-secret"}
        )
        state = self.context.state.load()
        self.assertNotIn("secret", state)
        self.assertEqual(state["feishu_secret_ref"], "synthetic-new-reference")
        self.context.services.start()
        with self.assertRaises(ValueError):
            self.service.handle("feishu-secret", {})

    def test_run_settings_and_login_service_use_explicit_ports(self):
        self.service.handle(
            "run-settings", {"send_enabled": False, "login_enabled": True}
        )
        self.assertTrue(self.context.login_enabled)
        self.service.handle("login-service", {"action": "uninstall"})
        self.assertFalse(self.context.login_enabled)
        before = self.context.state.load()
        with self.assertRaises(ValueError):
            self.service.handle(
                "run-settings", {"send_enabled": True, "login_enabled": "yes"}
            )
        self.assertEqual(before, self.context.state.load())


class ConnectionsUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.context = MemoryContext()
        self.service = ConnectionsUseCases(self.context)

    def test_prepare_status_and_test_only_need_the_agent_port(self):
        for action in ["connect-agent", "prepare-agent", "agent-status"]:
            self.assertTrue(self.service.handle(action, {})["ready"])
        result = self.service.handle("test-agent", {})
        self.assertEqual(result["provider_id"], "embedded-test")
        self.assertTrue(result["proposal"]["should_reply"])
        self.assertEqual(self.context.delivery.messages, [])
        self.assertEqual(self.context.credential_calls, [])

    def test_agent_isolation_cost_output_failure_and_timeout_are_actionable(self):
        for field, value in [
            ("tool_count", 1),
            ("cost_recorded", False),
            ("output", "{}"),
            ("job_status", "failed"),
            ("job_status", "running"),
        ]:
            with self.subTest(field=field):
                gateway = self.context.gateway
                previous = getattr(gateway, field)
                setattr(gateway, field, value)
                with self.assertRaises(RuntimeError):
                    self.service.test_agent()
                setattr(gateway, field, previous)
        self.context.gateway.cost_tracking = False
        self.context.gateway.cost_recorded = False
        self.assertEqual(self.service.test_agent()["tool_count"], 0)

    def test_delivery_test_requires_destination_and_exact_readback(self):
        self.assertTrue(self.service.handle("test-feishu", {})["readback"])
        self.assertEqual(
            len(self.service.handle("feishu-chats", {})["data"]["chats"]), 1
        )
        self.context.state.update(feishu_chat_id="")
        with self.assertRaises(ValueError):
            self.service.handle("test-feishu", {})
        self.assertTrue(
            self.service.handle("connection-check", {"discover_account": True})["ready"]
        )
        self.assertIn("agent", self.service.handle("auto-connect", {}))

    def test_running_services_reject_preparation_before_external_calls(self):
        self.context.services.start()
        for action in ["connect-agent", "prepare-agent", "auto-connect"]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.service.handle(action, {})
        self.assertEqual(self.context.gateway.calls, [])


class RuntimeUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.context = MemoryContext()
        self.service = RuntimeUseCases(self.context)

    def test_finish_setup_uses_generic_agent_and_starts_after_verified_delivery(self):
        self.context.state.update(chat_id="filehelper", group_name="文件传输助手")
        result = self.service.handle("finish-setup", {"send_enabled": False})
        self.assertTrue(result["running"])
        calls = self.context.action_calls
        self.assertLess(calls.index("verify-key"), calls.index("test-agent"))
        self.assertLess(calls.index("test-agent"), calls.index("test-feishu"))
        self.assertLess(calls.index("test-feishu"), calls.index("start"))
        self.assertTrue(self.service.handle("doctor", {})["synthetic"])
        self.assertEqual(self.service.handle("diagnostics", {})["profile_count"], 1)
        self.assertFalse(self.service.handle("stop", {})["running"])

    def test_invalid_credentials_stop_before_model_or_delivery(self):
        self.context.credential_request = lambda data: {"all_verified": False}
        with self.assertRaises(RuntimeError):
            self.service.handle("finish-setup", {})
        self.assertEqual(self.context.gateway.calls, [])
        self.assertEqual(self.context.delivery.messages, [])
        self.assertFalse(self.context.services.running())

    def test_start_checks_account_and_migration_agent_readiness(self):
        self.context.state.update(brand_bridge_pending=True)
        self.context.gateway.ready = False
        with self.assertRaises(RuntimeError):
            self.service.handle("start", {})
        self.assertFalse(self.context.services.running())
        self.context.gateway.ready = True
        self.assertTrue(self.service.handle("start", {})["running"])
        self.assertFalse(self.context.state.load()["brand_bridge_pending"])
        self.context.active = []
        with self.assertRaises(ValueError):
            self.service.handle("start", {})


class TaskUseCaseTests(unittest.TestCase):
    def test_review_and_cursor_reset_respect_runtime_state_and_confirmation(self):
        context = MemoryContext()
        service = TasksUseCases(context)
        context.tasks.save(
            {
                "id": "one",
                "status": "delivery_review",
                "agent_provider": "embedded-test",
            }
        )
        with self.assertRaises(ValueError):
            service.handle("task-review", {"id": "one", "decision": "retry"})
        service.handle(
            "task-review",
            {"id": "one", "decision": "mark-delivered", "confirmed": True},
        )
        self.assertEqual(context.tasks.task("one")["status"], "sent")
        with self.assertRaises(ValueError):
            service.handle("reset-cursor", {})
        self.assertTrue(service.handle("reset-cursor", {"confirmed": True})["saved"])
        context.services.start()
        with self.assertRaises(ValueError):
            service.review({"id": "one", "decision": "dismiss"})

    def test_every_application_group_rejects_unknown_operations(self):
        context = MemoryContext()
        for cls in [
            AccountsUseCases,
            CredentialsUseCases,
            SettingsUseCases,
            ConnectionsUseCases,
            RuntimeUseCases,
            TasksUseCases,
        ]:
            with self.subTest(group=cls.__name__), self.assertRaises(ValueError):
                cls(context).handle("not-an-operation", {})
