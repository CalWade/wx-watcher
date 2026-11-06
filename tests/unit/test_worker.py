import unittest
from pathlib import Path

from wx_watcher.application.replies import ReplyWorker
from wx_watcher.domain.agents import AgentJob

from .fakes import (
    EmbeddedGateway,
    ManualClock,
    MemoryDelivery,
    MemoryReader,
    MemoryTasks,
    reply_policy,
)


class ReplyWorkerUnitTests(unittest.TestCase):
    def setUp(self):
        self.reader = MemoryReader()
        self.agent = EmbeddedGateway()
        self.store = MemoryTasks()
        self.delivery = MemoryDelivery()
        self.clock = ManualClock()
        self.worker = ReplyWorker(
            reply_policy(),
            reader_root=Path("/synthetic/account/db_storage"),
            reader=self.reader,
            agent=self.agent,
            store=self.store,
            delivery=self.delivery,
            knowledge=lambda files, query: ("事实", ["合成资料"]),
            clock=self.clock,
        )
        self.worker.initialize()

    def test_embedded_provider_generates_a_draft_without_a_client_specific_dependency(
        self,
    ):
        self.reader.append("培训资料什么时候发送？")
        self.worker.tick()
        task = self.store.tasks()[0]
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["agent_provider"], "embedded-test")
        self.assertEqual(task["agent"]["provider_id"], "embedded-test")
        self.assertEqual(task["knowledge_sources"], ["合成资料"])
        self.worker.tick()
        self.assertEqual(
            len([call for call in self.agent.calls if call[0] == "submit"]), 1
        )
        self.assertEqual(self.worker.status()["agent"]["provider_id"], "embedded-test")

    def test_history_is_baselined_and_restarts_poll_the_original_job(self):
        self.reader.append("历史消息")
        store = MemoryTasks()
        self.worker.store = store
        self.worker.initialize()
        self.worker.tick()
        self.assertEqual(store.tasks(), [])
        self.agent.job_status = "running"
        self.reader.append("新增问题")
        self.worker.tick()
        task = store.tasks()[0]
        self.worker.initialize()
        self.agent.job_status = "completed"
        self.worker.tick()
        self.assertEqual(store.task(task["id"])["status"], "draft")
        self.assertEqual(
            len([call for call in self.agent.calls if call[0] == "submit"]), 1
        )

    def test_changing_provider_or_account_cannot_reuse_pending_execution(self):
        self.agent.job_status = "running"
        self.reader.append("问题")
        self.worker.tick()
        self.agent.provider_id = "another-engine"
        with self.assertRaisesRegex(RuntimeError, "内核不同"):
            self.worker.advance()
        self.agent.provider_id = "embedded-test"
        self.reader.status_value["scope_id"] = "different-account"
        with self.assertRaises(RuntimeError):
            self.worker.ingest()

    def test_non_questions_and_self_messages_do_not_submit_agent_jobs(self):
        for text, fields in [
            ("12345", {}),
            ("", {}),
            ("图片", {"type": 3}),
            ("自己发出的消息", {"is_self": True}),
        ]:
            self.reader.append(text, **fields)
        self.worker.tick()
        self.assertTrue(all(task["status"] == "skipped" for task in self.store.tasks()))
        self.assertFalse(any(call[0] == "submit" for call in self.agent.calls))

    def test_another_jobs_result_cannot_advance_the_task_or_reach_delivery(self):
        self.reader.append("问题")
        self.agent.submit = lambda id, prompt: AgentJob(
            "another-job",
            self.agent.provider_id,
            "completed",
            output=self.agent.output,
            tool_count=0,
        )
        with self.assertRaisesRegex(RuntimeError, "不匹配"):
            self.worker.tick()
        self.assertEqual(self.store.tasks()[0]["status"], "queued")
        self.assertEqual(self.delivery.messages, [])

    def test_batching_commits_one_task_and_survives_worker_reinitialization(self):
        self.worker.config = reply_policy(debounce_seconds=3, reply_rules="先确认资料")
        self.reader.append("第一个问题")
        self.reader.append("第二个问题")
        self.worker.ingest()
        self.assertEqual(self.store.tasks(), [])
        self.worker.initialize()
        self.clock.sleep(4)
        self.worker.tick()
        task = self.store.tasks()[0]
        self.assertEqual(len(task["event_ids"]), 2)
        self.assertIn("第一个问题\n第二个问题", task["event"]["content"])
        self.assertIn("先确认资料", task["prompt"])
        self.assertEqual(self.store.pending(), [])

    def test_daily_budget_invalid_output_and_agent_failure_require_review(self):
        self.worker.config = reply_policy(max_daily_calls=1)
        self.reader.append("问题一")
        self.worker.tick()
        self.reader.append("问题二")
        self.worker.tick()
        self.assertEqual(self.store.tasks()[-1]["status"], "budget_review")
        for status, output, tool_count in [
            ("failed", "", 0),
            ("interrupted", "", 0),
            ("completed", "{}", 0),
            ("completed", self.agent.output, 1),
        ]:
            with self.subTest(status=status, output=output[:8], tools=tool_count):
                self.store.records.clear()
                self.store.meta["daily_calls"] = {}
                self.agent.job_status, self.agent.output, self.agent.tool_count = (
                    status,
                    output,
                    tool_count,
                )
                self.reader.append("另一个问题")
                self.worker.tick()
                self.assertEqual(self.store.tasks()[0]["status"], "needs_review")

    def test_delivery_permissions_uncertainty_and_crash_recovery_are_explicit(self):
        self.reader.append("问题")
        self.worker.tick()
        task = self.store.tasks()[0]
        with self.assertRaises(PermissionError):
            self.worker.send(task)
        self.worker.send(task, dry_run=True)
        self.assertEqual(task["status"], "draft")
        self.worker.config = reply_policy(send_enabled=True)

        def fail(*args):
            raise RuntimeError("uncertain delivery")

        self.worker.feishu.send = fail
        with self.assertRaises(RuntimeError):
            self.worker.send(task)
        self.assertEqual(self.store.task(task["id"])["status"], "delivery_review")
        self.store.save({**task, "status": "sending"})
        self.worker.initialize()
        self.assertEqual(self.store.task(task["id"])["status"], "delivery_review")
        self.worker.feishu = self.delivery = MemoryDelivery()
        self.store.save({**task, "status": "draft"})
        self.worker.tick()
        self.assertEqual(self.store.task(task["id"])["status"], "sent")
        self.assertEqual(len(self.delivery.messages), 1)
