"""Reuse the user's existing clients and credentials during guided setup."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
from pathlib import Path


def dsh_application():
    for name in ["DSH NEXT", "DeepSeek Harness"]:
        app = Path("/Applications") / (name + ".app")
        if app.is_dir():
            return app
    raise FileNotFoundError("请先从 DSH 官方下载页面安装 Desktop，并在其中配置模型渠道")


def prepare_dsh(state):
    home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))).expanduser()
    profile = home / "profiles/desktop"
    package = profile / "node_modules/dsh-cost-meter/package.json"
    if package.is_file():
        from .reply import LocalAPI

        try:
            if (
                LocalAPI(
                    state.load()["dsh_port"], home / "storages/wx-reply-agent/token"
                )
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


def connect_existing(controller):
    result = {"checks": [], "accounts": [], "conversations": [], "feishu_chats": []}

    def step(stage, action):
        try:
            value = action()
            result["checks"].append({"stage": stage, "ok": True})
            return value
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            # Provider/package-manager output can contain private configuration.
            message = (
                str(error)
                if isinstance(
                    error,
                    (RuntimeError, ValueError, FileNotFoundError, PermissionError),
                )
                else "连接未完成，请检查对应客户端"
            )
            result["checks"].append(
                {"stage": stage, "ok": False, "message": message[:400]}
            )
            return None

    result["accounts"] = step("识别微信账号", controller.accounts) or []
    if controller.state.load()["profiles"]:
        result["credential"] = step(
            "复用并验证凭据", lambda: controller.perform("verify-key", {})
        )
        if result["credential"]:
            result["conversations"] = (
                step("读取会话列表", controller.conversations) or []
            )
    else:
        result["checks"].append(
            {
                "stage": "连接微信凭据",
                "ok": False,
                "message": "先导入已有密钥，或使用首次取钥向导",
            }
        )
    result["cost_meter"] = step(
        "复用或安装费用插件", lambda: prepare_dsh(controller.state)
    )
    if result["cost_meter"]:
        result["dsh"] = step("连接 DSH 桥接", controller.install_dsh)
        if result["dsh"] and not result["dsh"].get("ready"):
            result["checks"][-1].update(
                ok=False, message=result["dsh"].get("hint", "请先在 DSH 完成模型配置")
            )
    chats = step("读取飞书机器人所在群", lambda: controller.feishu().chats())
    result["feishu_chats"] = (chats or {}).get("data", {}).get("chats", [])
    return result
