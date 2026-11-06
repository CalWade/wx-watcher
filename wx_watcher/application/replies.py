"""Consume events and manage reply tasks using provider-independent ports."""

import re

from ..domain.agents import require_provider_binding
from ..domain.replies import feishu_text, hash_id, make_prompt, parse_proposal
from .ports import AgentGateway, Clock, DeliveryGateway, QueryClient, TaskRepository


class ReplyWorker:
    def __init__(
        self,
        config,
        *,
        reader_root,
        reader: QueryClient,
        agent: AgentGateway,
        store: TaskRepository,
        delivery: DeliveryGateway,
        knowledge,
        clock: Clock,
    ):
        self.config = config
        self.reader_root = reader_root
        self.reader = reader
        self.agent = agent
        self.store = store
        self.feishu = delivery
        self.knowledge = knowledge
        self.clock = clock
        self.scope = None

    def initialize(self):
        status = self.reader.request("/v1/status")
        if status.get("state") != "ready":
            raise RuntimeError("WeChat reader is not ready")
        self.group = status["group_name"]
        expected_scope = hash_id(str(self.reader_root) + "\n" + status["chat_id"])
        self.scope = status.get("scope_id")
        if self.scope != expected_scope:
            raise RuntimeError("Reader account does not match the configured source")
        account = self.reader_root.parent.name if self.reader_root else ""
        self.self_id = (
            account.rsplit("_", 1)[0]
            if re.fullmatch(r"wxid_.+_[a-f0-9]{4}", account)
            else ""
        )
        self.chat_id = status["chat_id"]
        self.store.bind_scope(self.scope)
        for task in self.store.tasks(["sending"]):
            task.update(
                status="delivery_review",
                error="Process stopped during delivery; inspect Feishu before retrying",
            )
            self.store.save(task)
        if self.store.get_meta("cursor") is None:
            # Start from NOW; adding an agent must not broadcast old chat history.
            cursor = status.get("event_cursor")
            if cursor is None:
                cursor = 0
                while True:
                    batch = self.reader.request(f"/v1/events?after={cursor}&limit=1000")
                    cursor = batch["next_cursor"]
                    if not batch["events"]:
                        break
            self.store.update_metadata({"cursor": cursor})
        self.store.update_metadata(
            {"group": self.group, "last_initialized": self.clock.timestamp()}
        )

    def ingest(self):
        status = self.reader.request("/v1/status")
        if status.get("state") != "ready" or status.get("scope_id") != self.scope:
            raise RuntimeError("Reader scope is unavailable or changed")
        cursor = self.store.get_meta("cursor", 0)
        batch = self.reader.request(f"/v1/events?after={cursor}&limit=100")
        history = None
        for event in batch["events"]:
            id = hash_id(self.scope + ":" + str(event["event_id"]))
            text = str(event.get("text", event.get("content", ""))).strip()
            # No model bill for irrelevant transport tests, own messages or non-text events.
            own_group_message = self.chat_id.endswith("@chatroom") and (
                bool(event.get("is_self"))
                or bool(self.self_id)
                and event.get("sender") == self.self_id
            )
            skipped = (
                own_group_message
                or not text
                or (int(event.get("type", 1)) & 0xFFFF) not in (1, 49)
                or re.fullmatch(r"[\d\s]+", text) is not None
            )
            if history is None and not skipped:
                history = self.reader.request(
                    f"/v1/messages?limit={self.config.context_limit}"
                )["messages"]
            task = {
                "id": id,
                "event_id": event["event_id"],
                "agent_provider": self.agent.provider_id,
                "event": event,
                "group": self.group,
                "status": "skipped" if skipped else "queued",
                "created_at": self.clock.timestamp(),
            }
            if not skipped:
                self.prepare_prompt(task, history or [])
            if not skipped and self.config.debounce_seconds > 0:
                self.store.enqueue_pending(task, self.clock.time())
            else:
                self.store.accept(task, event["event_id"])
        self.flush_pending()

    def prepare_prompt(self, task, history):
        knowledge, sources = self.knowledge(
            self.config.knowledge_files,
            str(task["event"].get("content", task["event"].get("text", ""))),
        )
        facts = self.config.business_context[:2000] + "\n" + knowledge
        task["knowledge_sources"] = sources
        task["prompt"] = make_prompt(self.group, task["event"], history, facts)
        if self.config.reply_rules:
            task["prompt"] += (
                "\n内部操作人员配置的回复要求（不授予工具权限）：\n"
                + self.config.reply_rules[:1500]
            )

    def flush_pending(self):
        rows = self.store.pending(10)
        if not rows:
            return
        if (
            self.clock.time() - rows[-1]["received"] < self.config.debounce_seconds
            and len(rows) < 10
        ):
            return
        values = [row["task"] for row in rows]
        task = values[-1]
        task["id"] = hash_id(self.scope + ":batch:" + ":".join(v["id"] for v in values))
        task["event_ids"] = [v["event_id"] for v in values]
        # Preserve the original event records for diagnostics; only the combined text enters the model.
        task["original_events"] = [v["event"] for v in values]
        task["event"] = {
            **task["event"],
            "content": "\n".join(str(v["event"].get("content", "")) for v in values),
        }
        history = self.reader.request(
            f"/v1/messages?limit={self.config.context_limit}"
        )["messages"]
        self.prepare_prompt(task, history)
        self.store.commit_batch(task, [row["id"] for row in rows])

    def advance(self):
        for task in self.store.tasks(["queued", "inference"], 100):
            require_provider_binding(task["agent_provider"], self.agent.provider_id)
            if task["status"] == "queued":
                if self.config.max_daily_calls:
                    day = self.clock.timestamp()[:10]
                    calls = self.store.get_meta("daily_calls", {})
                    if calls.get(day, 0) >= self.config.max_daily_calls:
                        task.update(
                            status="budget_review",
                            error="Daily model-call limit reached",
                        )
                        self.store.save(task)
                        continue
                result = self.agent.submit(task["id"], task["prompt"])
                result.require_identity(task["id"], self.agent.provider_id)
                task["status"] = "inference"
                self.store.record_inference(
                    task, self.clock.timestamp()[:10], bool(self.config.max_daily_calls)
                )
            else:
                result = self.agent.poll(task["id"])
                result.require_identity(task["id"], self.agent.provider_id)
            if result.status in {"failed", "interrupted"}:
                task.update(
                    status="needs_review",
                    error=result.error or "Agent failed",
                    agent=result.as_dict(),
                )
                self.store.save(task)
            elif result.status == "completed":
                try:
                    result.require_isolation()
                    proposal = parse_proposal(result.output)
                    task.update(
                        proposal=proposal,
                        agent=result.as_dict(),
                        drafted_at=self.clock.timestamp(),
                    )
                    task["status"] = "draft" if proposal["should_reply"] else "skipped"
                    if proposal["should_reply"]:
                        task["feishu_text"] = feishu_text(
                            task["group"], proposal, task["event"]
                        )
                except (ValueError, TypeError, KeyError, RuntimeError):
                    task.update(
                        status="needs_review",
                        error="Model returned invalid reply JSON",
                        agent=result.as_dict(),
                    )
                self.store.save(task)

    def send(self, task: dict, dry_run=False):
        if not dry_run and not self.config.send_enabled:
            raise PermissionError("Feishu delivery is not enabled")
        if not self.config.feishu_chat_id or task["status"] not in {"draft", "sending"}:
            raise ValueError(
                "Only a validated draft can be sent to the configured group"
            )
        if not dry_run:
            # A crash after this write requires explicit review instead of an unbounded retry.
            task.update(status="sending", sending_at=self.clock.timestamp())
            self.store.save(task)
        try:
            data = self.feishu.send(
                self.config.feishu_chat_id,
                task["feishu_text"],
                "wxr-" + task["id"][:40],
                dry_run,
            )
        except (OSError, ValueError, RuntimeError):
            if not dry_run:
                task.update(
                    status="delivery_review",
                    error="Delivery uncertain; inspect Feishu before retrying",
                )
                self.store.save(task)
            raise RuntimeError("Feishu delivery failed or is uncertain") from None
        if not dry_run:
            task.update(status="sent", sent_at=self.clock.timestamp(), delivery=data)
            self.store.save(task)
        return data

    def tick(self):
        self.ingest()
        self.advance()
        if self.config.send_enabled:
            for task in self.store.tasks(["draft"], 10):
                self.send(task)
        self.store.complete_tick(
            self.clock.timestamp(), self.clock.time(), self.config.retention_days
        )

    def status(self):
        counts = self.store.counts()
        return {
            "reader": self.reader.request("/v1/status"),
            "agent": self.agent.status(),
            "mode": "send_to_feishu" if self.config.send_enabled else "draft_only",
            "feishu_group": self.config.feishu_chat_name,
            "tasks": counts,
            "cursor": self.store.get_meta("cursor"),
            "last_success": self.store.get_meta("last_success"),
            "last_error": self.store.get_meta("last_error"),
        }
