"""DSH Desktop installation, SDK layout and cost-meter setup."""

import hashlib
import json
import os
import plistlib
import re
import socket
import subprocess
import time
from pathlib import Path

from ...appstate import atomic_text
from ...branding import BRIDGE_ID, LEGACY_BRIDGE_ID, VERSION, dsh_storage
from ...source import private_dir
from ..http import LocalAPI


class DshDesktop:
    def __init__(self, state, client):
        self.state = state
        self.dsh = client

    def install(self):
        home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
        profile = home / "profiles/desktop"
        if not profile.is_dir():
            raise ValueError("未找到 DSH desktop 配置，请先安装并启动支持的 DSH 版本")
        from ...migration import migrate_dsh_storage, validate_move

        validate_move(home / "storages/wx-reply-agent", home / "storages/notebridge")
        source = Path(__file__).parents[2] / "assets/dsh/index.mjs"
        if not source.exists():
            source = Path(__file__).resolve().parents[3] / "integrations/dsh/index.mjs"
        content = source.read_bytes()
        release = hashlib.sha256(content).hexdigest()[:16]
        current = {}
        try:
            current = self.dsh().request("/v1/status")
            if (
                current.get("ready")
                and current.get("source_digest", "")[:16] == release
            ):
                return {
                    "installed": True,
                    "build": release,
                    "ready": True,
                    "reused": True,
                }
        except (OSError, RuntimeError):
            pass
        target = self.state.home / "integrations/dsh" / release / "index.mjs"
        private_dir(target.parent)
        target.write_bytes(content)
        patch = profile / "cordis.patch.yml"
        old = patch.read_text() if patch.exists() else ""
        backup = self.state.home / "backups" / ("dsh-" + str(int(time.time())) + ".yml")
        private_dir(backup.parent)
        backup.write_text(old)
        backup.chmod(0o600)
        lines = old.splitlines(keepends=True)
        owned_pattern = (
            rf"\s*- id: (?:{re.escape(BRIDGE_ID)}|{re.escape(LEGACY_BRIDGE_ID)})\s*$"
        )
        matches = [i for i, line in enumerate(lines) if re.match(owned_pattern, line)]
        if len(matches) > 1:
            raise ValueError(
                "同时发现多份新旧桥接条目，请先核对并只保留本项目条目；未修改其他配置"
            )
        start = next(
            (i for i, line in enumerate(lines) if re.match(owned_pattern, line)),
            None,
        )
        indent, end = 4, len(lines)
        if start is not None:
            deadline = time.monotonic() + 30
            while current.get("active", 0):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "DSH 仍在完成已有回复任务，请完成后重试迁移；桥接尚未停用"
                    )
                time.sleep(0.5)
                current = self.dsh().request("/v1/status")
            indent = len(lines[start]) - len(lines[start].lstrip())
            end = start + 1
            while end < len(lines):
                line = lines[end]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                end += 1
            disabled = (
                lines[:start]
                + [
                    " " * indent + "- id: notebridge\n",
                    " " * (indent + 2) + "name: " + target.as_uri() + "\n",
                    " " * (indent + 2) + "disabled: true\n",
                ]
                + lines[end:]
            )
            atomic_text(patch, "".join(disabled))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(
                        ("127.0.0.1", self.state.load()["dsh_port"]), timeout=0.3
                    ):
                        pass
                except OSError:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(
                    "DSH 尚未卸载旧桥接。请在插件管理中停用本项目条目后重试；未重启其他会话。"
                )
        migrate_dsh_storage(home)
        row = [
            " " * indent + "- id: notebridge\n",
            " " * (indent + 2) + "name: " + target.as_uri() + "\n",
            " " * (indent + 2) + "config:\n",
            " " * (indent + 4) + "port: " + str(self.state.load()["dsh_port"]) + "\n",
        ]
        if start is not None:
            # Preserve unrelated settings that may have changed while waiting for disposal.
            fresh = patch.read_text().splitlines(keepends=True)
            current = next(
                (i for i, line in enumerate(fresh) if re.match(owned_pattern, line)),
                None,
            )
            if current is None:
                raise RuntimeError("桥接配置被同时修改，请重试")
            finish = current + 1
            while finish < len(fresh):
                line = fresh[finish]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                finish += 1
            updated = "".join(fresh[:current] + row + fresh[finish:])
        else:
            updated = old + "\n- insert:\n" + "".join(row)
        atomic_text(patch, updated)
        endtime = time.monotonic() + 20
        while time.monotonic() < endtime:
            try:
                status = self.dsh().request("/v1/status")
                if (
                    status.get("build") == VERSION
                    and status.get("source_digest", "")[:16] == release
                ):
                    return {"installed": True, "build": release, "ready": True}
            except (OSError, RuntimeError):
                pass
            time.sleep(0.5)
        return {
            "installed": True,
            "ready": False,
            "hint": "请在 DSH 中检查插件状态，并确认 dsh-cost-meter 已安装",
        }

    @staticmethod
    def prepare(prepare_cost, install_bridge):
        cost = prepare_cost()
        bridge = install_bridge()
        ready = bool(bridge.get("ready"))
        return {
            "provider_id": "dsh",
            "name": "DSH",
            "ready": ready,
            "capabilities": {"cost_tracking": True, "session_history": True},
            "cost_meter": cost,
            "checks": [
                {"stage": "费用插件", "ok": bool(cost.get("installed"))},
                {
                    "stage": "DSH 模型连接",
                    "ok": ready,
                    "message": bridge.get("hint", ""),
                },
            ],
            "message": "DSH 与费用插件已连接"
            if ready
            else bridge.get("hint", "请在 DSH 完成模型配置"),
        }


def dsh_application():
    for name in ["DSH NEXT", "DeepSeek Harness"]:
        app = Path("/Applications") / (name + ".app")
        if app.is_dir():
            return app
    raise FileNotFoundError("请先从 DSH 官方下载页面安装 Desktop，并在其中配置模型渠道")


def prepare_cost_meter(state, client_factory=LocalAPI):
    home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))).expanduser()
    profile = home / "profiles/desktop"
    package = profile / "node_modules/dsh-cost-meter/package.json"
    if package.is_file():
        try:
            if (
                client_factory(state.load()["dsh_port"], dsh_storage(home) / "token")
                .request("/v1/status")
                .get("ready")
            ):
                return {
                    "installed": True,
                    "reused": True,
                    "version": json.loads(package.read_text())["version"],
                }
        except (OSError, RuntimeError):
            pass
    app = dsh_application()
    subprocess.run(
        ["/usr/bin/open", "-g", "-a", str(app)],
        capture_output=True,
        check=True,
        timeout=10,
    )
    if package.is_file():
        return {
            "installed": True,
            "reused": True,
            "version": json.loads(package.read_text())["version"],
        }
    deadline = time.monotonic() + 20
    while not profile.is_dir() and time.monotonic() < deadline:
        time.sleep(0.25)
    if not profile.is_dir():
        raise RuntimeError("请先在 DSH Desktop 完成首次启动和模型配置")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    binary = app / "Contents/MacOS" / info["CFBundleExecutable"]
    entry = app / "Contents/Resources/app.asar/lib/plugin-cli.js"
    # Use the installed Desktop's official package manager and bundled pnpm.
    # No global Node packages, private provider profiles or ledger files are replaced.
    env = {**os.environ, "ELECTRON_RUN_AS_NODE": "1", "DSH_HOME": str(home)}
    result = subprocess.run(
        [str(binary), str(entry), "desktop", "add", "dsh-cost-meter@1.9.16"],
        env=env,
        cwd=Path.home(),
        capture_output=True,
        text=True,
        timeout=240,
    )
    if result.returncode or not package.is_file():
        raise RuntimeError(
            "DSH 未能自动安装费用插件，请检查其插件管理器；本版本仅自动适配已验证的 Desktop 入口"
        )
    return {
        "installed": True,
        "reused": False,
        "version": json.loads(package.read_text())["version"],
    }
