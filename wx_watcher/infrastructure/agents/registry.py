"""Explicit factories avoid importing provider code from business use cases."""

from collections.abc import Callable
from copy import deepcopy

from ...application.ports import AgentGateway


class AgentRegistry:
    def __init__(self):
        self._validators = {}
        self._factories: dict[str, Callable[[dict], AgentGateway]] = {}

    def register(
        self, provider_id: str, factory: Callable[[dict], AgentGateway], validate=None
    ):
        if not provider_id or provider_id in self._factories:
            raise ValueError("Agent provider must have a unique identifier")
        self._factories[provider_id] = factory
        self._validators[provider_id] = validate

    def create(self, provider_id: str, options: dict) -> AgentGateway:
        self.validate(provider_id, options)
        gateway = self._factories[provider_id](deepcopy(options))
        if gateway.provider_id != provider_id:
            raise ValueError("Agent factory returned a different provider")
        return gateway

    def validate(self, provider_id, options):
        if provider_id not in self._factories:
            raise ValueError("尚未安装所选 Agent 适配器")
        if not isinstance(options, dict):
            raise ValueError("Agent 配置无效")
        validator = self._validators[provider_id]
        if validator is not None:
            validator(options)

    def providers(self):
        return tuple(self._factories)
