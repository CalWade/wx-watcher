"""The composition root is the only place that selects production adapters."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .application.ports import (
    CaptureGateway,
    Clock,
    DeliveryGateway,
    SettingsRepository,
    Supervisor,
    TaskAccess,
)
from .appstate import AppState
from .branding import dsh_storage
from .feishu import Feishu
from .infrastructure.agents.dsh import DshGateway, validate_options
from .infrastructure.agents.http import HttpAgentGateway
from .infrastructure.agents.registry import AgentRegistry
from .infrastructure.clock import SystemClock
from .infrastructure.desktop_resources import DesktopResources
from .infrastructure.task_access import SqliteTaskAccess
from .keyflow import KeyFlow
from .reply import LocalAPI
from .services import Services


@dataclass
class DesktopDependencies:
    state: SettingsRepository
    services: Supervisor
    resources: DesktopResources
    capture: CaptureGateway
    tasks: TaskAccess
    clock: Clock
    credentials: Callable[[dict], dict]
    model_client: Callable[[], LocalAPI]
    delivery_client: Callable[[], DeliveryGateway]
    agent_factory: Callable
    agent_registry: AgentRegistry


def build_desktop(
    directory, account_guard, credentials, prepare_agent=None, legacy_client=None
):
    state = AppState(directory)

    def model_client():
        options = state.load()["agent_options"]
        return LocalAPI(
            options.get("port", state.load()["dsh_port"]),
            Path(options.get("token_file", dsh_storage() / "token")).expanduser(),
        )

    def delivery_client():
        settings = state.load()
        return Feishu(
            settings["feishu_transport"],
            settings["feishu_app_id"],
            settings["feishu_secret_ref"],
        )

    registry = agent_registry(
        lambda options: DshGateway(
            legacy_client() if legacy_client else model_client(), prepare_agent
        ),
    )

    def agent_factory():
        settings = state.load()
        return registry.create(
            settings.get("agent_provider", "dsh"), settings.get("agent_options", {})
        )

    return DesktopDependencies(
        state=state,
        services=Services(state, account_guard=account_guard),
        resources=DesktopResources(state, model_client),
        capture=KeyFlow(state),
        tasks=SqliteTaskAccess(state),
        clock=SystemClock(),
        credentials=credentials,
        model_client=model_client,
        delivery_client=delivery_client,
        agent_factory=agent_factory,
        agent_registry=registry,
    )


def build_reply_dependencies(
    config,
    *,
    reader=None,
    agent=None,
    store=None,
    delivery=None,
    knowledge=None,
    clock=None,
):
    from .config import Config
    from .infrastructure.http import LocalAPI
    from .infrastructure.tasks import ReplyStore
    from .knowledge import select_passages

    reader_config = Config.load(config.reader_config)
    if agent is None:
        registry = agent_registry(
            lambda options: DshGateway(
                LocalAPI(
                    options.get("port", config.dsh_port),
                    Path(options.get("token_file", config.dsh_token_file)),
                )
            ),
        )
        agent = registry.create(
            getattr(config, "agent_provider", "dsh"),
            getattr(config, "agent_options", {}),
        )
    return {
        "reader_root": reader_config.db_root.resolve(),
        "reader": reader
        if reader is not None
        else LocalAPI(reader_config.port, reader_config.state_dir / "api-token.txt"),
        "agent": agent,
        "store": store if store is not None else ReplyStore(config.state_dir),
        "delivery": delivery
        if delivery is not None
        else Feishu(
            config.feishu_transport,
            config.feishu_app_id,
            config.feishu_secret_ref,
            config.lark_cli,
        ),
        "knowledge": knowledge if knowledge is not None else select_passages,
        "clock": clock if clock is not None else SystemClock(),
    }


def agent_registry(dsh_factory):
    """Both desktop setup and worker processes use the same registered providers."""
    registry = AgentRegistry()
    registry.register("dsh", dsh_factory, validate=validate_options)
    registry.register(
        "http",
        lambda options: HttpAgentGateway(
            LocalAPI(options["port"], Path(options["token_file"]).expanduser())
        ),
        validate=validate_http_options,
    )
    return registry


def build_reader_dependencies(config, *, source=None, clock=None):
    from .source import WeChatSource

    return {
        "source": source if source is not None else WeChatSource(config),
        "clock": clock if clock is not None else SystemClock(),
    }


def validate_http_options(options):
    if set(options) != {"port", "token_file"}:
        raise ValueError("HTTP Agent 需要端口和凭据文件引用")
    if type(options["port"]) is not int or not 1024 <= options["port"] <= 65535:
        raise ValueError("HTTP Agent 端口无效")
    if (
        not isinstance(options["token_file"], str)
        or not Path(options["token_file"]).is_absolute()
    ):
        raise ValueError("HTTP Agent 凭据文件引用无效")
