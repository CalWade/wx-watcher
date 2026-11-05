"""Reuse the user's existing clients and credentials during guided setup."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
from pathlib import Path

from .branding import dsh_storage


def check_first_connection(controller, requested_root="", discover_account=False):
    """Read-only checks; never launch authorization, a debugger, or a capture job."""
    import errno

    from .credentials import native_request

    denied = False
    try:
        accounts = controller.accounts()
    except PermissionError:
        accounts, denied = [], True
    active = [a for a in accounts if a.get("active")]
    chosen = (
        active[0] if len(active) == 1 else accounts[0] if len(accounts) == 1 else None
    )
    login_account = bool(discover_account) or (
        not requested_root and not accounts and not denied
    )
    root = (
        ""
        if login_account
        else (
            str(Path(requested_root).expanduser().resolve())
            if requested_root
            else (chosen["db_root"] if chosen else "")
        )
    )
    request = {"operation": "connection_preflight"}
    if root:
        request["db_root"] = root
    native = native_request(request)
    denied = denied or native.get("data_directory_errno") in {errno.EACCES, errno.EPERM}
    denied = denied or native.get("database_access_errno") in {
        errno.EACCES,
        errno.EPERM,
    }
    checks = []

    def check(id, title, ok, message, action=""):
        checks.append(
            dict(id=id, title=title, ok=bool(ok), message=message, action=action)
        )

    installed = native.get("wechat_installed", False)
    official = native.get("official_signature", False) and native.get(
        "hardened_runtime", False
    )
    check(
        "client",
        "官方微信",
        installed and official,
        "已找到官方签名客户端"
        if installed and official
        else "请先安装并正常打开官方 Mac 微信；本向导不提供微信安装包。",
        "wechat",
    )
    supported = (
        native.get("tested_build", False)
        and native.get("tested_os", False)
        and native.get("tested_architecture", False)
    )
    check(
        "version",
        "取钥兼容范围",
        supported,
        "当前系统与微信构建在已验证范围内"
        if supported
        else "自动取钥仅放行 Apple Silicon、macOS 27.0、微信 4.1.13（269630）。当前微信："
        + (native.get("wechat_version") or "未识别")
        + "；其他组合尚未验证，请勿反复授权或登录尝试。",
    )
    debugger = bool(native.get("lldb")) and native.get("debugger_compatible", False)
    check(
        "debugger",
        "调试工具",
        debugger,
        "兼容的 LLDB 已就绪"
        if debugger
        else "请安装适用的 Apple Xcode 并完成首次打开；macOS 27 需要 LLDB 2000 或以上，旧版命令行工具可能不适用。安装后重新检查。",
        "xcode",
    )
    access_ready = not denied and native.get(
        "data_access_ready", native.get("database_readable", False)
    )
    check(
        "access",
        "微信资料读取权限",
        access_ready,
        "已确认读取权限"
        if access_ready
        else "请在完整磁盘访问权限允许本应用。列表中没有时，将权限帮助窗口中的应用图标拖入列表，开启后点「重新连接」。",
        "privacy",
    )
    initialized = bool(root) and native.get("database_readable", False)
    needs_choice = not login_account and not root and not denied
    if not access_ready:
        database_message = (
            "读取权限尚未确认，暂时不能判断本地资料状态；无需先登录或发送消息。"
        )
    elif login_account:
        database_message = (
            "登录后自动等待微信生成本地资料并验证，无需预先登录或发送测试消息。"
        )
    elif needs_choice:
        database_message = "已找到多个历史账号，请在下方选择；也可以连接其他账号。"
    elif initialized:
        database_message = "已有本地数据库，可直接开始取钥，无需先登录。"
    elif native.get("database_access_errno") not in {
        None,
        0,
        errno.ENOENT,
        errno.ENOTDIR,
    }:
        database_message = (
            native.get("permission_hint")
            or "所选数据库读取出现错误，请检查目录与运行诊断。"
        )
    else:
        database_message = "所选目录尚未生成完整资料，登录该账号后将自动等待并验证。"
    missing_database = native.get("database_access_errno") in {
        None,
        0,
        errno.ENOENT,
        errno.ENOTDIR,
    }
    database_ok = access_ready and (
        login_account or bool(root) and (initialized or missing_database)
    )
    check("database", "本地消息资料", database_ok, database_message)
    account_ok = access_ready and (login_account or bool(root))
    label = (
        "登录时自动识别"
        if login_account
        else next(
            (a["label"] for a in accounts if a["db_root"] == root), "已选择账号目录"
        )
        if root
        else "请选择账号"
    )
    message = (
        "监听准备好后登录你要连接的账号，程序自动识别本次账号。"
        if login_account
        else label + "；取钥时登录这个账号即可，无需提前登录。"
        if root
        else "请选择下方的历史账号，或选择「登录其他账号」。"
    )
    if not access_ready:
        message = "授权后自动识别本机账号；无需预先登录。"
    check("account", "本次连接账号", account_ok, message)
    return {
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        "db_root": root,
        "account_label": label,
        "account_mode": "login" if login_account else "existing",
        "needs_account_choice": needs_choice,
        "accounts": accounts,
        "native": native,
    }


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
                LocalAPI(state.load()["dsh_port"], dsh_storage(home) / "token")
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
    current = [a for a in result["accounts"] if a.get("active")]
    profile = next(
        (
            p
            for p in controller.state.load()["profiles"]
            if len(current) == 1
            and Path(p["db_root"]).resolve() == Path(current[0]["db_root"]).resolve()
        ),
        None,
    )
    if profile:
        if profile["id"] != controller.state.load()["active_profile"]:
            step(
                "切换当前登录账号",
                lambda: controller.perform(
                    "settings", {"active_profile": profile["id"]}
                ),
            )
        if controller.state.load()["active_profile"] == profile["id"]:
            result["credential"] = step(
                "复用并验证凭据", lambda: controller.perform("verify-key", {})
            )
        if result.get("credential"):
            result["conversations"] = (
                step("读取会话列表", controller.conversations) or []
            )
    else:
        result["checks"].append(
            {
                "stage": "连接微信凭据",
                "ok": False,
                "message": "当前登录账号没有可复用的凭据，或尚未确认登录账号。请在账号区域连接当前账号，不会改用其他历史账号。",
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
