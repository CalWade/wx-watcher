"""Load legacy and current reply configuration at the file boundary."""

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..branding import dsh_storage
from ..config import Config


@dataclass(frozen=True)
class ReplyConfig:
    reader_config: Path
    state_dir: Path
    dsh_token_file: Path
    feishu_chat_id: str = ""
    feishu_chat_name: str = ""
    send_enabled: bool = False
    context_limit: int = 20
    poll_interval: float = 1
    lark_cli: str = "/opt/homebrew/bin/lark-cli"
    business_context: str = ""
    dsh_port: int = 8766
    knowledge_files: tuple[Path, ...] = ()
    reply_rules: str = ""
    debounce_seconds: float = 0
    max_daily_calls: int = 0
    feishu_transport: str = "cli"
    feishu_app_id: str = ""
    feishu_secret_ref: str = ""
    retention_days: int = 0
    agent_provider: str = "dsh"
    agent_options: dict = field(default_factory=dict)

    @classmethod
    def load(cls, file: Path):
        data = tomllib.loads(file.read_text())
        root = file.resolve().parent.parent

        def local(value):
            p = Path(value).expanduser()
            return p.resolve() if p.is_absolute() else (root / p).resolve()

        result = cls(
            reader_config=local(data.get("reader_config", "config/local.toml")),
            state_dir=local(data.get("state_dir", ".runtime/agent")),
            dsh_token_file=local(
                data.get("dsh_token_file", str(dsh_storage() / "token"))
            ),
            feishu_chat_id=data.get("feishu_chat_id", ""),
            feishu_chat_name=data.get("feishu_chat_name", ""),
            send_enabled=data.get("send_enabled", False),
            context_limit=int(data.get("context_limit", 20)),
            poll_interval=float(data.get("poll_interval", 1)),
            lark_cli=data.get("lark_cli", "/opt/homebrew/bin/lark-cli"),
            business_context=data.get("business_context", ""),
            dsh_port=int(data.get("dsh_port", 8766)),
            knowledge_files=tuple(local(x) for x in data.get("knowledge_files", [])),
            reply_rules=data.get("reply_rules", ""),
            debounce_seconds=float(data.get("debounce_seconds", 0)),
            max_daily_calls=int(data.get("max_daily_calls", 0)),
            feishu_transport=data.get("feishu_transport", "cli"),
            feishu_app_id=data.get("feishu_app_id", ""),
            feishu_secret_ref=data.get("feishu_secret_ref", ""),
            retention_days=int(data.get("retention_days", 0)),
            agent_provider=data.get("agent_provider", "dsh"),
            agent_options=json.loads(data.get("agent_options_json", "{}")),
        )
        if not isinstance(result.agent_options, dict) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", result.agent_provider
        ):
            raise ValueError("Invalid agent provider configuration")
        if not 1 <= result.context_limit <= 50 or not 0.2 <= result.poll_interval <= 30:
            raise ValueError("Invalid context_limit or poll_interval")
        if not 1024 <= result.dsh_port <= 65535:
            raise ValueError("Invalid DSH port")
        if (
            not 0 <= result.debounce_seconds <= 30
            or not 0 <= result.max_daily_calls <= 10000
        ):
            raise ValueError("Invalid batching or daily limit")
        if result.feishu_transport not in {"cli", "openapi"}:
            raise ValueError("Invalid Feishu transport")
        if not 0 <= result.retention_days <= 10000:
            raise ValueError("Invalid retention period")
        if result.feishu_chat_id and not re.fullmatch(
            r"oc_[a-zA-Z0-9]+", result.feishu_chat_id
        ):
            raise ValueError("Invalid Feishu group ID")
        if result.send_enabled and not result.feishu_chat_id:
            raise ValueError("Sending requires a fixed Feishu group")
        reader = Config.load(result.reader_config)
        if reader.db_root is None:
            raise ValueError(
                "Pin the verified reader db_root before starting the agent"
            )
        # Reuse the source/state separation checks already enforced by the reader.
        Config(
            db_root=reader.db_root, key_file=reader.key_file, state_dir=result.state_dir
        )
        if result.state_dir == reader.state_dir:
            raise ValueError("Agent state must be separate from the reader state")
        return result
