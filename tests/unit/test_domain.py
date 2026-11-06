import json
import unittest

from wx_watcher.domain.account_names import clean_label, display_name
from wx_watcher.domain.accounts import AccountConnection, require_account_match
from wx_watcher.domain.agents import AgentJob, require_provider_binding
from wx_watcher.domain.replies import feishu_text, make_prompt, parse_proposal
from wx_watcher.domain.settings import validate_settings
from wx_watcher.domain.tasks import review_task

from .fakes import PROPOSAL


class AccountRulesTests(unittest.TestCase):
    def test_readiness_requires_saved_data_and_unambiguous_identity(self):
        for selected, count, current, saved, available, denied, configure, live in [
            ("a", 0, None, True, True, False, True, False),
            ("a", 1, "a", True, True, False, True, True),
            ("a", 1, "b", True, True, False, False, False),
            ("a", 2, None, True, True, False, False, False),
            ("a", 0, None, False, True, False, False, False),
            ("a", 0, None, True, False, False, False, False),
            ("a", 0, None, True, True, True, False, False),
            (None, 0, None, True, True, False, False, False),
        ]:
            with self.subTest(
                count=count,
                current=current,
                selected=selected,
                saved=saved,
                available=available,
                denied=denied,
            ):
                connection = AccountConnection(
                    current, selected, count, saved, available, denied
                )
                self.assertEqual(connection.can_configure, configure)
                self.assertEqual(connection.matches_current, live)

    def test_local_configuration_and_live_monitoring_have_distinct_guards(self):
        require_account_match("a", [], live=False)
        require_account_match("a", ["a"], live=True)
        for active, live in [([], True), (["b"], False), (["a", "b"], False)]:
            with self.subTest(active=active), self.assertRaises(ValueError):
                require_account_match("a", active, live=live)

    def test_names_fall_back_and_never_change_identity(self):
        root = "/synthetic/wxid_owner_abcd/db_storage"
        self.assertEqual(display_name(root)["label"], "未命名账号 · abcd")
        self.assertEqual(
            display_name(root, {"nickname": "昵称", "wechat_id": "account"})["label"],
            "昵称 · account",
        )
        self.assertEqual(
            display_name(root, {"nickname": "same", "wechat_id": "same"})["label"],
            "same",
        )
        self.assertEqual(
            display_name(root, {"wechat_id": "account"})["label"], "account"
        )
        self.assertEqual(
            display_name(root, profile={"label": "工作号"})["label"], "工作号"
        )
        self.assertEqual(clean_label(" 👩‍💻 工作号 "), "👩‍💻 工作号")
        for value in [42, "a\nb", "x" * 81, "\ud800"]:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                clean_label(value)


class SettingsRulesTests(unittest.TestCase):
    def test_invalid_changes_are_rejected_before_mutation(self):
        profiles = [{"id": "a"}]
        invalid = [
            {"reader_port": True},
            {"reader_port": 1023},
            {"active_profile": "b"},
            {"chat_id": "bad/id"},
            {"send_enabled": 1},
            {"autostart_monitor": "true"},
            {"max_daily_calls": -1},
            {"retention_days": 10001},
            {"debounce_seconds": 31},
            {"reply_rules": []},
            {"business_context": "x" * 20001},
            {"feishu_transport": "invalid"},
            {"agent_provider": "../x"},
            {"agent_options": []},
            {"agent_options": {"text": "x" * 10001}},
        ]
        for values in invalid:
            before = dict(values)
            with self.subTest(values=list(values)), self.assertRaises(ValueError):
                validate_settings(values, profiles, "a")
            self.assertEqual(values, before)

    def test_known_settings_are_copied_and_profile_switch_is_explicit(self):
        values = {
            "active_profile": "b",
            "reader_port": 3456,
            "max_daily_calls": 10,
            "debounce_seconds": 0.5,
            "agent_provider": "embedded",
            "agent_options": {"model": "example"},
            "secret": "ignored",
        }
        accepted, switching = validate_settings(values, [{"id": "a"}, {"id": "b"}], "a")
        self.assertTrue(switching)
        self.assertNotIn("secret", accepted)
        accepted["agent_options"]["model"] = "different"
        self.assertEqual(values["agent_options"]["model"], "example")


class ReplyRulesTests(unittest.TestCase):
    def test_model_schema_rejects_malformed_and_unbounded_results(self):
        proposal = json.loads(PROPOSAL)
        self.assertEqual(parse_proposal("```json\n" + PROPOSAL + "\n```"), proposal)
        variants = [
            [],
            {**proposal, "should_reply": 1},
            {**proposal, "summary": "x" * 401},
            {**proposal, "needs_confirmation": "x"},
            {**proposal, "needs_confirmation": ["x"] * 11},
            {**proposal, "needs_confirmation": [42]},
            {**proposal, "should_reply": False},
            {**proposal, "suggested_reply": ""},
        ]
        for value in variants:
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                parse_proposal(json.dumps(value))

    def test_prompt_filters_future_and_duplicate_messages_and_bounds_context(self):
        event = {
            "created_at": 10,
            "local_id": 2,
            "message_id": "trigger",
            "content": "问题",
        }
        history = [
            {
                "created_at": 10,
                "local_id": 1,
                "message_id": str(i),
                "content": "x" * 2000,
            }
            for i in range(100)
        ]
        history += [event, {"created_at": 11, "content": "未来内容"}]
        prompt = make_prompt("合成群", event, history, "事实")
        data = json.loads(prompt.split("\n", 1)[1])
        self.assertLess(len(prompt), 28000)
        self.assertNotIn("未来内容", prompt)
        self.assertNotIn("trigger", json.dumps(data["recent_messages"]))
        text = feishu_text("群", json.loads(PROPOSAL), {"content": "<at>用户</at>"})
        self.assertNotIn("<at>", text)
        self.assertIn("资料发送时间", text)

    def test_agent_contract_rejects_invalid_jobs_and_tool_execution(self):
        for changes in [
            {"status": "unknown"},
            {"id": ""},
            {"provider_id": ""},
            {"output": 42},
            {"output": "x" * 100001},
            {"tool_count": -1},
            {"tool_count": True},
            {"id": 1},
            {"status": []},
            {"usage": []},
            {"cost": None},
            {"session_ref": 1},
            {"error": "x" * 601},
        ]:
            values = {
                "id": "job",
                "provider_id": "embedded",
                "status": "completed",
                **changes,
            }
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                AgentJob(**values)
        for count in [None, 1]:
            with self.assertRaises(RuntimeError):
                AgentJob(
                    "job", "embedded", "completed", tool_count=count
                ).require_isolation()
        AgentJob("job", "embedded", "completed", tool_count=0).require_isolation()
        job = AgentJob("job", "embedded", "completed")
        job.require_identity("job", "embedded")
        for id, provider in [("other", "embedded"), ("job", "different")]:
            with self.assertRaises(RuntimeError):
                job.require_identity(id, provider)
        require_provider_binding("embedded", "embedded")
        with self.assertRaises(RuntimeError):
            require_provider_binding("old", "new")


class TaskRulesTests(unittest.TestCase):
    def test_review_requires_confirmation_and_preserves_original_until_commit(self):
        task = {"id": "one", "status": "delivery_review", "agent_provider": "embedded"}
        for decision in [
            {"decision": "retry"},
            {"decision": "mark-delivered"},
            {"decision": "invalid"},
        ]:
            with self.assertRaises(ValueError):
                review_task(task, decision, timestamp="now", retry_id="two")
            self.assertEqual(task["status"], "delivery_review")
        sent = review_task(
            task,
            {"decision": "mark-delivered", "confirmed": True},
            timestamp="now",
            retry_id="two",
        )
        self.assertEqual(sent[0]["status"], "sent")
        retry = review_task(
            task,
            {"decision": "retry", "confirmed_not_delivered": True},
            timestamp="now",
            retry_id="two",
        )
        self.assertEqual(retry[0]["status"], "draft")
        self.assertEqual(
            review_task(task, {"decision": "dismiss"}, timestamp="now", retry_id="two")[
                0
            ]["status"],
            "dismissed",
        )

    def test_model_retry_creates_a_new_bound_task_without_old_execution_state(self):
        task = {
            "id": "one",
            "status": "needs_review",
            "agent_provider": "embedded",
            "prompt": "q",
            "agent": {"session_ref": "old"},
            "proposal": {},
            "error": "old",
        }
        retry, original = review_task(
            task, {"decision": "retry"}, timestamp="now", retry_id="two"
        )
        self.assertEqual(retry["agent_provider"], "embedded")
        self.assertNotIn("agent", retry)
        self.assertNotIn("error", retry)
        self.assertEqual(original["replacement_id"], "two")
        self.assertEqual(task["status"], "needs_review")
        for value in [None, {"status": "queued"}]:
            with self.assertRaises(ValueError):
                review_task(
                    value, {"decision": "retry"}, timestamp="now", retry_id="two"
                )
