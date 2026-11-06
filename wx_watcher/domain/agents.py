"""Provider-independent jobs crossing the agent boundary."""

from dataclasses import asdict, dataclass, field

JOB_STATES = frozenset({"queued", "running", "completed", "failed", "interrupted"})


@dataclass(frozen=True)
class AgentJob:
    id: str
    provider_id: str
    status: str
    output: str = ""
    usage: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    tool_count: int | None = None
    session_ref: str = ""
    error: str = ""

    def __post_init__(self):
        if (
            not isinstance(self.status, str)
            or self.status not in JOB_STATES
            or not isinstance(self.id, str)
            or not 1 <= len(self.id) <= 200
            or not isinstance(self.provider_id, str)
            or not 1 <= len(self.provider_id) <= 80
        ):
            raise ValueError("Agent returned an invalid job identity or status")
        if not isinstance(self.output, str) or len(self.output) > 100_000:
            raise ValueError("Agent output exceeds the supported limit")
        if self.tool_count is not None and (
            type(self.tool_count) is not int or self.tool_count < 0
        ):
            raise ValueError("Agent returned an invalid tool count")
        if not isinstance(self.usage, dict) or not isinstance(self.cost, dict):
            raise ValueError("Agent returned invalid usage or cost metadata")
        if (
            not isinstance(self.session_ref, str)
            or len(self.session_ref) > 1000
            or not isinstance(self.error, str)
            or len(self.error) > 600
        ):
            raise ValueError("Agent returned invalid diagnostic metadata")

    def as_dict(self) -> dict:
        return asdict(self)

    def require_isolation(self):
        if self.tool_count != 0:
            raise RuntimeError("Agent 工具隔离未通过，请检查所接入的 Agent")

    def require_identity(self, id: str, provider_id: str):
        if self.id != id or self.provider_id != provider_id:
            raise RuntimeError("Agent 返回的任务与当前请求不匹配")


def require_provider_binding(bound_provider: str, current_provider: str):
    if bound_provider != current_provider:
        raise RuntimeError("任务绑定的 Agent 与当前内核不同，请先处理已有任务后再切换")
