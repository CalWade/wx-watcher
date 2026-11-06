"""All DSH job, session and cost-meter protocol translation lives here."""

from ...domain.agents import AgentJob


class DshGateway:
    provider_id = "dsh"

    def __init__(self, client, prepare=None):
        self.client = client
        self._prepare = prepare

    def prepare(self):
        if self._prepare is not None:
            return self._prepare()
        return self.status()

    def status(self):
        status = self.client.request("/v1/status")
        return {
            "provider_id": self.provider_id,
            "name": "DSH",
            "ready": bool(status.get("ready")),
            "capabilities": {"cost_tracking": True, "session_history": True},
        }

    def submit(self, id, prompt):
        return self._job(
            id, self.client.request("/v1/jobs", {"id": id, "prompt": prompt})
        )

    def poll(self, id):
        return self._job(id, self.client.request("/v1/jobs/" + id))

    def _job(self, id, value):
        if value.get("id", id) != id:
            raise ValueError("Agent returned another job's result")
        meter = value.get("cost_meter") or {}
        return AgentJob(
            id=id,
            provider_id=self.provider_id,
            status=value["status"],
            output=value.get("output", ""),
            usage=value.get("usage") or {},
            cost={
                "recorded": bool(meter.get("found")),
                "amount": (meter.get("own") or {}).get("cost"),
                "currency": "USD",
            },
            tool_count=value.get("request_tool_count", value.get("visible_tools")),
            session_ref=value.get("session_id", ""),
            error=str(value.get("error", ""))[:600],
        )


def validate_options(options):
    port = options.get("port", 8766)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Agent 本地端口无效")
    if set(options) - {"port", "token_file"}:
        raise ValueError("DSH 适配器包含不支持的配置项")
    if "token_file" in options and not isinstance(options["token_file"], str):
        raise ValueError("Agent 凭据引用无效")
