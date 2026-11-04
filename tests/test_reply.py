import json
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_pipeline import TABLE, Fixture

from wx_watcher.api import create_server, load_token
from wx_watcher.collector import Collector
from wx_watcher.reply import (
    ReplyAgent,
    ReplyConfig,
    ReplyStore,
    feishu_text,
    make_prompt,
    parse_proposal,
)
from wx_watcher.store import Store

PROPOSAL = {
    "should_reply": True,
    "summary": "发送时间",
    "suggested_reply": "我先确认具体安排，再给您准确回复。",
    "reason": "缺少发送日期，需要内部确认",
    "needs_confirmation": ["具体发送时间"],
}


class FakeDSH:
    def __init__(self):
        self.jobs = {}
        self.post_count = 0

    def request(self, route, data=None):
        if data:
            self.post_count += 1
            self.jobs[data["id"]] = {
                "id": data["id"],
                "status": "completed",
                "output": json.dumps(PROPOSAL, ensure_ascii=False),
                "session_id": "wx-reply-" + data["id"],
                "usage": {"input": 20, "output": 30},
            }
            return {"id": data["id"], "status": "queued"}
        return self.jobs[route.rsplit("/", 1)[1]]


class ReplyValidationTests(unittest.TestCase):
    def test_output_schema_rejects_instructions_and_invalid_values(self):
        self.assertEqual(parse_proposal(json.dumps(PROPOSAL)), PROPOSAL)
        for value in [
            {**PROPOSAL, "command": "rm -rf /"},
            {**PROPOSAL, "should_reply": "true"},
            {**PROPOSAL, "suggested_reply": ""},
            {**PROPOSAL, "should_reply": False},
            {**PROPOSAL, "needs_confirmation": [None]},
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_proposal(json.dumps(value))

    def test_prompt_excludes_later_messages_and_bounds_context(self):
        history = [
            {
                "message_id": str(i),
                "local_id": i,
                "created_at": 100,
                "content": "早期上下文" * 600,
            }
            for i in range(50)
        ]
        event = {
            "message_id": "trigger",
            "local_id": 49,
            "created_at": 100,
            "content": "什么时候发资料？",
        }
        history += [{"created_at": 101, "content": "这个是未来消息，不应出现在请求中"}]
        prompt = make_prompt("样例群", event, history, "价格未确定" * 2000)
        self.assertLess(len(prompt), 28000)
        self.assertNotIn("这个是未来消息", prompt)
        self.assertIn("什么时候发资料", prompt)
        self.assertNotIn("master-key", prompt)

    def test_feishu_text_cannot_create_mentions_or_fetch_remote_images(self):
        value = {
            **PROPOSAL,
            "suggested_reply": '<at user_id="all">所有人</at> ![x](https://example.com/x.png)',
        }
        text = feishu_text("群", value, {"content": "消息"})
        self.assertNotIn("<at", text)
        self.assertIn(
            "https://example.com/x.png", text
        )  # Delivered as text, never Markdown-fetched.

    def test_store_cursor_and_tasks_survive_restart_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state"
            store = ReplyStore(p)
            store.bind_scope("scope-a")
            task = {"id": "a", "event_id": 7, "status": "draft"}
            store.accept(task, 7)
            store.accept(task, 7)
            store.db.close()
            resumed = ReplyStore(p)
            self.assertEqual(len(resumed.tasks()), 1)
            self.assertEqual(resumed.get_meta("cursor"), 7)
            self.assertEqual((p / "tasks.sqlite3").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                resumed.bind_scope("scope-b")
            resumed.db.close()


class ReplyPipelineTests(unittest.TestCase):
    """Real SQLCipher/WAL → authenticated reader → durable agent → mocked model boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root, encrypted=True)
        self.reader_store = Store(self.fixture.config.state_dir)
        self.collector = Collector(self.fixture.config, self.reader_store)
        self.collector.poll()
        token = load_token(self.fixture.config.state_dir)
        self.server = create_server(self.reader_store, self.collector, token, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        reader_config = self.root / "config/local.toml"
        reader_config.parent.mkdir()
        reader_config.write_text(
            f"db_root = {json.dumps(str(self.fixture.root))}\n"
            f"state_dir = {json.dumps(str(self.fixture.config.state_dir))}\n"
            f"key_file = {json.dumps(str(self.fixture.config.key_file))}\n"
            f"port = {self.server.server_port}\n"
        )
        dsh_token = self.root / "dsh-token"
        dsh_token.write_text("x" * 64)
        dsh_token.chmod(0o600)
        self.config = ReplyConfig(
            reader_config=reader_config,
            state_dir=self.root / "agent",
            dsh_token_file=dsh_token,
            feishu_chat_id="oc_test",
            feishu_chat_name="测试内部群",
        )
        self.agent = ReplyAgent(self.config)
        self.fake = FakeDSH()
        self.agent.dsh = self.fake
        self.agent.initialize()

    def tearDown(self):
        self.agent.store.db.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.reader_store.close()
        self.fixture.close()
        self.temp.cleanup()

    def add(self, id, text):
        self.fixture.insert(self.fixture.shard, id, text)
        self.collector.poll()

    def test_new_message_runs_once_and_cost_record_stays_attached(self):
        self.assertEqual(self.agent.store.tasks(), [])
        self.add(2, "培训资料什么时候发给我们？")
        self.agent.tick()
        self.assertEqual(self.fake.post_count, 1)
        self.agent.tick()
        task = self.agent.store.tasks()[0]
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["proposal"], PROPOSAL)
        self.assertIn("usage", task["dsh"])
        self.agent.initialize()
        self.agent.tick()
        self.assertEqual(self.fake.post_count, 1)
        self.assertEqual(len(self.agent.store.tasks()), 1)

    def test_restart_inflight_polls_original_job_without_model_resubmission(self):
        self.add(2, "资料何时发送？")
        self.agent.tick()
        self.agent.store.db.close()
        self.agent = ReplyAgent(self.config)
        self.agent.dsh = self.fake
        self.agent.initialize()
        self.agent.tick()
        self.assertEqual(self.fake.post_count, 1)
        self.assertEqual(self.agent.store.tasks()[0]["status"], "draft")

    def test_numeric_and_system_events_do_not_call_model(self):
        self.add(2, "609517")
        self.fixture.insert(self.fixture.shard, 3, "系统通知")
        self.fixture.shard.exec(
            f'UPDATE "{TABLE}" SET local_type=10000 WHERE local_id=3;'
        )
        self.collector.poll()
        self.agent.tick()
        tasks = self.agent.store.tasks()
        self.assertEqual([t["status"] for t in tasks], ["skipped", "skipped"])
        self.assertEqual(self.fake.post_count, 0)

    def test_own_group_messages_do_not_generate_reply_suggestions(self):
        self.agent.self_id = "wxid_alice"
        self.add(2, "我们明天发给您")
        self.agent.tick()
        self.assertEqual(self.agent.store.tasks()[0]["status"], "skipped")
        self.assertEqual(self.fake.post_count, 0)

    def test_reader_scope_change_blocks_ingest(self):
        self.collector.update_status(scope_id="different-account")
        with self.assertRaises(RuntimeError):
            self.agent.ingest()

    def test_invalid_model_output_is_held_for_review(self):
        self.add(2, "什么时候发？")
        self.agent.tick()
        next(iter(self.fake.jobs.values()))["output"] = '{"command":"send this"}'
        self.agent.tick()
        self.assertEqual(self.agent.store.tasks()[0]["status"], "needs_review")

    def test_delivery_is_fixed_bot_plain_text_and_not_repeated(self):
        self.add(2, "什么时候发？")
        self.agent.tick()
        self.agent.tick()
        task = self.agent.store.tasks()[0]
        with self.assertRaises(PermissionError):
            self.agent.send(task)
        self.agent.config = replace(self.config, send_enabled=True)
        success = subprocess.CompletedProcess(
            [], 0, stdout='{"ok":true,"data":{"message_id":"m1"}}', stderr=""
        )
        with patch("wx_watcher.reply.subprocess.run", return_value=success) as run:
            self.agent.send(task)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("--as") + 1], "bot")
            self.assertEqual(args[args.index("--chat-id") + 1], "oc_test")
            self.assertNotIn("--markdown", args)
            self.assertTrue(
                args[args.index("--idempotency-key") + 1].startswith("wxr-")
            )
            self.agent.tick()
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.agent.store.task(task["id"])["status"], "sent")

    def test_uncertain_delivery_is_not_automatically_retried(self):
        self.add(2, "什么时候发？")
        self.agent.tick()
        self.agent.tick()
        task = self.agent.store.tasks()[0]
        self.agent.config = replace(self.config, send_enabled=True)
        with patch(
            "wx_watcher.reply.subprocess.run",
            side_effect=subprocess.TimeoutExpired("lark-cli", 30),
        ) as run:
            with self.assertRaises(RuntimeError):
                self.agent.send(task)
            self.agent.tick()
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.agent.store.task(task["id"])["status"], "delivery_review")

    def test_crash_during_delivery_is_held_on_restart(self):
        self.add(2, "什么时候发？")
        self.agent.tick()
        task = self.agent.store.tasks()[0]
        task["status"] = "sending"
        self.agent.store.save(task)
        self.agent.initialize()
        self.assertEqual(self.agent.store.task(task["id"])["status"], "delivery_review")

    def test_batch_survives_restart_and_combines_consecutive_messages(self):
        self.agent.config = replace(self.config, debounce_seconds=2)
        self.add(2, "请问培训时间？")
        self.add(3, "还有上课地点？")
        self.agent.tick()
        self.assertEqual(self.fake.post_count, 0)
        self.agent.store.db.close()
        self.agent = ReplyAgent(replace(self.config, debounce_seconds=2))
        self.agent.dsh = self.fake
        self.agent.initialize()
        with self.agent.store.db:
            self.agent.store.db.execute(
                "UPDATE pending_events SET received=?", (time.time() - 3,)
            )
        self.agent.tick()
        self.assertEqual(self.fake.post_count, 1)
        task = self.agent.store.tasks()[0]
        self.assertEqual(len(task["event_ids"]), 2)
        self.assertIn("培训时间", task["event"]["content"])
        self.assertIn("上课地点", task["event"]["content"])

    def test_daily_call_limit_holds_extra_task(self):
        self.agent.config = replace(self.config, max_daily_calls=1)
        self.add(2, "第一个问题是什么时候？")
        self.add(3, "第二个问题在哪里？")
        self.agent.tick()
        self.assertEqual(self.fake.post_count, 1)
        self.assertIn("budget_review", [t["status"] for t in self.agent.store.tasks()])


if __name__ == "__main__":
    unittest.main()
