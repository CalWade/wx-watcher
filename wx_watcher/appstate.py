"""Per-user application state, independent of checkout location."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from .branding import app_home, dsh_storage
from .infrastructure.agents.config import merge_agent_settings, normalize_agent_settings
from .source import private_dir


def atomic_json(path: Path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def atomic_text(path: Path, value: str):
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


class AppState:
    def __init__(self, directory: Path | None = None):
        self.home = directory or app_home()
        self.home = self.home.expanduser().resolve()
        protected = Path.home() / "Library/Containers/com.tencent.xinWeChat"
        if self.home in {Path("/"), Path.home().resolve()} or self.home.is_relative_to(
            protected
        ):
            raise ValueError("应用数据目录必须独立，不能使用用户根目录或微信容器")
        private_dir(self.home)
        self.file = self.home / "settings.json"
        self.lock = threading.RLock()
        if not self.file.exists():
            atomic_json(
                self.file,
                {
                    "version": 1,
                    "profiles": [],
                    "active_profile": "",
                    "chat_id": "",
                    "group_name": "",
                    "business_context": "",
                    "knowledge_files": [],
                    "reply_rules": "",
                    "feishu_transport": "cli"
                    if shutil.which("lark-cli")
                    or Path("/opt/homebrew/bin/lark-cli").is_file()
                    else "openapi",
                    "feishu_chat_id": "",
                    "feishu_chat_name": "",
                    "feishu_app_id": "",
                    "feishu_secret_ref": "",
                    "dsh_port": 8766,
                    "reader_port": 18765,
                    "send_enabled": False,
                    "autostart_monitor": False,
                    "debounce_seconds": 2,
                    "max_daily_calls": 100,
                    "retention_days": 30,
                },
            )

    def load(self) -> dict:
        with self.lock:
            return normalize_agent_settings(json.loads(self.file.read_text()))

    def update(self, **values):
        with self.lock:
            settings = self.load()
            settings.update(merge_agent_settings(settings, values))
            atomic_json(self.file, settings)
            return settings

    def profile(self) -> dict:
        state = self.load()
        matches = [p for p in state["profiles"] if p["id"] == state["active_profile"]]
        if len(matches) != 1:
            raise ValueError("请先连接微信账号并验证密钥")
        return matches[0]

    def account_display(self, root, settings=None):
        from .account_names import display_name

        root = str(Path(root).expanduser().resolve())
        settings = settings if settings is not None else self.load()
        profile = next((p for p in settings["profiles"] if p["db_root"] == root), None)
        return display_name(root, settings.get("account_names", {}).get(root), profile)

    def save_account_names(self, root, **values):
        from .account_names import clean_label

        root = str(Path(root).expanduser().resolve())
        if "custom_label" in values:
            values["custom_label"] = clean_label(values["custom_label"])
        with self.lock:
            state = self.load()
            names = state.get("account_names", {})
            entry = names.setdefault(root, {})
            entry.setdefault(
                "custom_label", self.account_display(root, state)["custom_label"]
            )
            entry.update(values)
            state["account_names"] = names
            label = self.account_display(root, state)["label"]
            for profile in state["profiles"]:
                if profile["db_root"] == root:
                    profile["label"] = label
            self.update(account_names=names, profiles=state["profiles"])
            return self.account_display(root)

    def add_profile(self, root: Path, key_ref: str, label="", verification=None):
        root = root.expanduser().resolve()
        if not (root / "contact/contact.db").is_file():
            raise ValueError("所选目录不是有效的微信 db_storage")
        with self.lock:
            state = self.load()
            if label:
                self.save_account_names(root, custom_label=label)
                state = self.load()
            profile = {
                "id": str(uuid.uuid4()),
                "db_root": str(root),
                "key_ref": key_ref,
                "label": self.account_display(root, state)["label"],
                "verification": verification or {},
            }
            profiles = [p for p in state["profiles"] if p["db_root"] != str(root)]
            profiles.append(profile)
            return self.update(
                profiles=profiles,
                active_profile=profile["id"],
                chat_id="",
                group_name="",
                send_enabled=False,
                autostart_monitor=False,
            )

    def write_configs(self) -> tuple[Path, Path]:
        state, profile = self.load(), self.profile()
        if not state.get("chat_id"):
            raise ValueError("请先选择要监听的微信会话")
        scope = (
            self.home / "profiles" / profile["id"] / state["chat_id"].replace("@", "_")
        )
        private_dir(scope)
        config = self.home / "config"
        private_dir(config)

        def write(file, values):
            lines = []
            for k, value in values.items():
                lines.append(
                    k
                    + " = "
                    + (
                        str(value).lower()
                        if isinstance(value, bool)
                        else json.dumps(value, ensure_ascii=False)
                    )
                )
            atomic_text(file, "\n".join(lines) + "\n")

        reader = config / "reader.toml"
        write(
            reader,
            {
                "db_root": profile["db_root"],
                "key_ref": profile["key_ref"],
                "group_name": state["group_name"],
                "chat_id": state["chat_id"],
                "state_dir": str(scope / "reader"),
                "port": state["reader_port"],
                "poll_interval": 1.0,
                "retention_days": state["retention_days"],
            },
        )
        reply = config / "reply.toml"
        write(
            reply,
            {
                "reader_config": str(reader),
                "state_dir": str(scope / "agent"),
                "dsh_token_file": str(dsh_storage() / "token"),
                "dsh_port": state["dsh_port"],
                "agent_provider": state.get("agent_provider", "dsh"),
                "agent_options_json": json.dumps(state.get("agent_options", {})),
                "feishu_chat_id": state["feishu_chat_id"],
                "feishu_chat_name": state["feishu_chat_name"],
                "send_enabled": state["send_enabled"],
                "business_context": state["business_context"],
                "knowledge_files": state["knowledge_files"],
                "reply_rules": state["reply_rules"],
                "debounce_seconds": state["debounce_seconds"],
                "max_daily_calls": state["max_daily_calls"],
                "retention_days": state["retention_days"],
                "feishu_transport": state["feishu_transport"],
                "feishu_app_id": state["feishu_app_id"],
                "feishu_secret_ref": state["feishu_secret_ref"],
            },
        )
        return reader, reply
