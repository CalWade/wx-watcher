"""Authenticated command facade. Business use cases and I/O adapters are composed separately."""

from __future__ import annotations

import socket as socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .application.accounts import AccountsUseCases
from .application.connections import ConnectionsUseCases
from .application.credentials import CredentialsUseCases
from .application.runtime import RuntimeUseCases
from .application.settings import SettingsUseCases
from .application.tasks import TasksUseCases
from .composition import DesktopDependencies, build_desktop
from .credentials import native_request as native_request


class Controller:
    def __init__(
        self, directory=None, *, dependencies: DesktopDependencies | None = None
    ):
        self.dependencies = dependencies or build_desktop(
            directory,
            self.require_current_account,
            lambda data: native_request(data),
            self.prepare_agent,
            lambda: self.dsh(),
        )
        self.dependencies.resources.dsh = lambda: self.dsh()
        self.dependencies.resources.agent_status = lambda: self.agent().status()
        self.state = self.dependencies.state
        self.services = self.dependencies.services
        self.capture = self.dependencies.capture
        self.tasks = self.dependencies.tasks
        self.clock = self.dependencies.clock
        self.pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="notebridge-setup"
        )
        self.jobs = {}
        self.lock = threading.RLock()
        self.account_use_cases = AccountsUseCases(self)
        self.task_use_cases = TasksUseCases(self)
        groups = [
            self.account_use_cases,
            CredentialsUseCases(self),
            SettingsUseCases(self),
            ConnectionsUseCases(self),
            RuntimeUseCases(self),
            self.task_use_cases,
        ]
        self.handlers = {
            operation: group.handle
            for group in groups
            for operation in group.operations
        }

    def submit(self, operation, data):
        id = str(uuid.uuid4())
        with self.lock:
            for old in list(self.jobs):
                if len(self.jobs) < 100:
                    break
                if self.jobs[old]["status"] != "running":
                    del self.jobs[old]
            self.jobs[id] = {
                "id": id,
                "operation": operation,
                "status": "running",
                "started": time.time(),
            }

        def run():
            try:
                value = self.perform(operation, data)
                self.jobs[id].update(status="completed", result=value)
            except Exception as exc:
                # Only our own bounded diagnostic messages reach the interface.
                message = (
                    str(exc)
                    if isinstance(
                        exc,
                        (ValueError, RuntimeError, FileNotFoundError, PermissionError),
                    )
                    else type(exc).__name__
                )
                self.jobs[id].update(status="failed", error=message[:600])
            self.jobs[id]["finished"] = time.time()

        self.pool.submit(run)
        return {"job_id": id}

    def perform(self, operation, data):
        # Keep account/configuration changes atomic with service start and stop.
        if operation in {
            "settings",
            "account-name",
            "refresh-account-name",
            "import-key",
            "restore-key",
            "feishu-secret",
            "start",
            "stop",
            "task-review",
            "reset-cursor",
            "auto-connect",
            "connect-dsh",
            "connect-agent",
            "prepare-agent",
            "install-dsh",
            "finish-setup",
            "run-settings",
        }:
            with self.lock:
                return self._perform(operation, data)
        return self._perform(operation, data)

    def _perform(self, operation, data):
        aliases = {
            "connect-dsh": "connect-agent",
            "test-dsh": "test-agent",
            "install-dsh": "prepare-agent",
        }
        canonical = aliases.get(operation, operation)
        if canonical == "settings" and "dsh_port" in data:
            data = {
                **data,
                "agent_options": {
                    **self.state.load()["agent_options"],
                    **data.get("agent_options", {}),
                    "port": data["dsh_port"],
                },
            }
        handler = self.handlers.get(canonical)
        if handler is None:
            raise ValueError("未知操作")
        result = handler(canonical, data)
        if operation == "test-dsh":
            return {
                **result,
                "tools": result["tool_count"],
                "cost_meter": {
                    "found": result["cost"].get("recorded"),
                    "own": {"cost": result["cost"].get("amount")},
                },
                "session_id": result["session_ref"],
            }
        return result

    def account_status(self):
        return self.account_use_cases.account_status()

    def require_current_account(self, profile=None):
        return self.account_use_cases.require_current_account(profile)

    def require_local_account(self):
        return self.account_use_cases.require_local_account()

    def conversations(self, query=""):
        return self.account_use_cases.conversations(query)

    def read_conversations(self, query=""):
        return self.dependencies.resources.conversations(self.reader(), query)

    def accounts(self):
        return self.dependencies.resources.accounts()

    def active_roots(self):
        return self.dependencies.resources.active_roots()

    def canonical_root(self, value):
        return Path(value).expanduser().resolve()

    def credential_request(self, data):
        return self.dependencies.credentials(data)

    def load_key_file(self, value):
        from .source import load_master

        return load_master(Path(value).expanduser())

    def refresh_account_names(self, root, quiet=False):
        return self.dependencies.resources.refresh_account_names(root, quiet)

    def reader(self):
        return self.dependencies.resources.reader()

    @property
    def preview_reader(self):
        return self.dependencies.resources.preview_reader

    def close_preview(self):
        resources = self.dependencies.resources
        if resources.preview_reader is not None:
            resources.preview_reader.close()
        resources.preview_reader = None

    def validate_agent(self, provider_id, options):
        self.dependencies.agent_registry.validate(provider_id, options)

    def agent(self):
        return self.dependencies.agent_factory()

    def dsh(self):
        return self.dependencies.model_client()

    def feishu(self):
        return self.dependencies.delivery_client()

    def install_dsh(self):
        with self.lock:
            return self._install_dsh()

    def _install_dsh(self):
        return self.dependencies.resources._install_dsh()

    def doctor(self):
        return self.dependencies.resources.doctor()

    def records(self):
        return self.tasks.recent()

    def review_task(self, data):
        return self.task_use_cases.review(data)

    def select_passages(self, files, query):
        from .knowledge import select_passages

        return select_passages(files, query)

    def assert_data_access(self):
        try:
            with (Path(self.state.profile()["db_root"]) / "contact/contact.db").open(
                "rb"
            ):
                pass
        except PermissionError:
            raise PermissionError(
                "请在完整磁盘访问权限中允许 NoteBridge，再点击重新连接；已有密钥无需重取。"
            ) from None

    def login_installed(self):
        from .branding import login_file

        return login_file().exists()

    def manage_login(self, action):
        from .desktop import manage_login

        return manage_login(action, self.state.home, quiet=True)

    def prepare_agent(self):
        return self.dependencies.resources.prepare_agent(
            self.prepare_dsh, self.install_dsh
        )

    def prepare_dsh(self):
        from .onboarding import prepare_dsh

        return prepare_dsh(self.state)

    def connect_existing(self):
        from .onboarding import connect_existing

        return connect_existing(self)

    def check_first_connection(self, root, discover):
        from .onboarding import check_first_connection

        return check_first_connection(self, root, discover)
