"""Application commands for Runtime; concrete adapters are supplied by composition."""

from __future__ import annotations

from .ports import RuntimeContext


class RuntimeUseCases:
    operations = ("finish-setup", "start", "stop", "doctor", "diagnostics")

    def __init__(self, context: RuntimeContext):
        self.context = context

    def handle(self, operation, data):
        if operation == "finish-setup":
            self.context.require_current_account()
            self.context.perform("run-settings", data)
            verified = self.context.perform("verify-key", {})
            if not verified.get("all_verified"):
                raise RuntimeError("数据库凭据尚未全部验证通过，请先检查账号与数据权限")
            model = self.context.perform("test-agent", {})
            if model.get("tool_count") != 0:
                raise RuntimeError("模型工具隔离检查未通过，请检查 Agent 连接")
            delivery = self.context.perform("test-feishu", {})
            services = self.context.perform("start", {})
            return {
                "model_tools": model["tool_count"],
                "cost_recorded": bool(model.get("cost", {}).get("recorded")),
                "readback": delivery["readback"],
                "running": services["running"],
                "message": "模型、费用和飞书回读检查通过，消息助手已启动",
            }
        if operation == "doctor":
            return self.context.doctor()
        if operation == "start":
            self.context.require_current_account()
            if self.context.state.load().get("brand_bridge_pending"):
                self.context.assert_data_access()
                if not self.context.agent().prepare().get("ready"):
                    raise RuntimeError("Agent 迁移尚未就绪，请检查所接入的 Agent")
                self.context.state.update(brand_bridge_pending=False)
            result = self.context.services.start()
            if result.get("running"):
                self.context.state.update(migration_resume_requested=False)
            return result
        if operation == "stop":
            return self.context.services.stop()
        if operation == "diagnostics":
            s = self.context.state.load()
            return {
                "version": "0.4.5",
                "environment": self.context.doctor(),
                "services": self.context.services.status(),
                "profile_count": len(s["profiles"]),
                "destination_configured": bool(s["feishu_chat_id"]),
                "record_counts": {
                    k: sum(r["status"] == k for r in self.context.records())
                    for k in ["sent", "draft", "needs_review", "delivery_review"]
                },
            }
        raise ValueError("未知操作")
