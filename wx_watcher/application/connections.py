"""Connect and test agent and delivery ports through the same application routes."""

import hashlib
import json
import uuid

from ..domain.replies import parse_proposal
from .ports import ConnectionsContext


class ConnectionsUseCases:
    operations = (
        "connect-agent",
        "agent-status",
        "test-agent",
        "prepare-agent",
        "auto-connect",
        "feishu-chats",
        "test-feishu",
        "connection-check",
    )

    def __init__(self, context: ConnectionsContext):
        self.context = context

    def handle(self, operation, data):
        if operation in {"connect-agent", "prepare-agent"}:
            if self.context.services.running():
                raise ValueError("请先在工作台暂停监控，再连接或更新模型")
            return self.context.agent().prepare()
        if operation == "agent-status":
            return self.context.agent().status()
        if operation == "auto-connect":
            if self.context.services.running():
                raise ValueError("请先暂停监控，再检查或更新连接")
            return self.context.connect_existing()
        if operation == "feishu-chats":
            return self.context.feishu().chats()
        if operation == "test-feishu":
            settings = self.context.state.load()
            if not settings["feishu_chat_id"]:
                raise ValueError("请先选择飞书接收群")
            client = self.context.feishu()
            text = "【NoteBridge 连通性测试】机器人接入正常。本消息由你在本地应用中点击测试后发送。"
            result = client.send(
                settings["feishu_chat_id"], text, "wxt-" + uuid.uuid4().hex
            )
            message_id = result["data"]["message_id"]
            client.confirm_readback(message_id, text)
            return {"sent": True, "readback": True, "message_id": message_id}
        if operation == "test-agent":
            return self.test_agent()
        if operation == "connection-check":
            return self.context.check_first_connection(
                str(data.get("db_root", "")), data.get("discover_account") is True
            )
        raise ValueError("未知操作")

    def test_agent(self):
        gateway = self.context.agent()
        id = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        prompt = json.dumps(
            {
                "conversation": "连接测试",
                "trigger_message": {"text": "请问培训几点开始？"},
                "business_facts": "尚未提供培训时间，请提示内部确认。",
            },
            ensure_ascii=False,
        )
        job = gateway.submit(id, prompt)
        job.require_identity(id, gateway.provider_id)
        deadline = self.context.clock.monotonic() + 125
        while (
            job.status in {"queued", "running"}
            and self.context.clock.monotonic() < deadline
        ):
            self.context.clock.sleep(0.5)
            job = gateway.poll(id)
            job.require_identity(id, gateway.provider_id)
        if job.status != "completed":
            raise RuntimeError("模型测试未完成，请查看 Agent 运行记录")
        job.require_isolation()
        capabilities = gateway.status().get("capabilities", {})
        if capabilities.get("cost_tracking") and not job.cost.get("recorded"):
            raise RuntimeError("Agent 费用记录检查未通过")
        try:
            proposal = parse_proposal(job.output)
        except (ValueError, TypeError, KeyError):
            raise RuntimeError(
                "模型测试没有返回有效的回复建议格式，请检查 Agent 运行记录"
            ) from None
        return {**job.as_dict(), "proposal": proposal}
