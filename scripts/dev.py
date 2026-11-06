#!/usr/bin/env python3
"""Bootstrap, run and verify a private synthetic application environment."""

import argparse
import importlib.util
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
CURRENT = ROOT / ".runtime/dev/current.json"
OWNED_PROCESSES = {}


def doctor(native=False):
    from wx_watcher.infrastructure.sqlcipher import library_available

    checks = {
        name: bool(shutil.which(name)) for name in ("uv", "node", "npm", "sqlcipher")
    }
    checks["python"] = sys.version_info >= (3, 12)
    checks["coverage"] = importlib.util.find_spec("coverage") is not None
    checks["sqlcipher_library"] = library_available()
    checks["browser_dependencies"] = (
        ROOT / "node_modules/@playwright/test/package.json"
    ).is_file()
    checks["browser_runtime"] = False
    if checks["node"] and checks["browser_dependencies"]:
        try:
            checks["browser_runtime"] = (
                subprocess.run(
                    [
                        "node",
                        "-e",
                        "process.exit(require('fs').existsSync(require('@playwright/test').chromium.executablePath()) ? 0 : 1)",
                    ],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if native:
        checks["macos"] = sys.platform == "darwin"
        checks["swift"] = bool(shutil.which("swift"))
        checks["codesign"] = bool(shutil.which("codesign"))
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "platform": platform.system(),
        "python": platform.python_version(),
        "synthetic": True,
    }


def bootstrap(native=False):
    if not shutil.which("node") or not shutil.which("npm"):
        raise RuntimeError("请先安装 Node.js（包含 npm），再重试 bootstrap")
    missing = [name for name in ("uv", "sqlcipher") if not shutil.which(name)]
    if missing:
        if sys.platform != "darwin" or not shutil.which("brew"):
            raise RuntimeError("请先安装 uv、SQLCipher 及其动态库，再重试 bootstrap")
        subprocess.run(["brew", "install", *missing], check=True, cwd=ROOT)
    subprocess.run(["uv", "sync", "--frozen", "--group", "build"], check=True, cwd=ROOT)
    subprocess.run(["npm", "ci", "--ignore-scripts"], check=True, cwd=ROOT)
    subprocess.run(
        ["npx", "--no-install", "playwright", "install", "chromium"],
        check=True,
        cwd=ROOT,
    )
    if native:
        if sys.platform != "darwin" or not shutil.which("swift"):
            raise RuntimeError("原生构建需要在 Mac 安装适用的 Apple 开发者工具")
        subprocess.run(
            ["swift", "build", "--package-path", "native", "-c", "release"],
            check=True,
            cwd=ROOT,
        )
    return {"completed": True, "native_built": native}


def request(descriptor, route, data=None):
    from support.sandbox import TOKEN

    base = descriptor["base_url"]
    headers = {
        "Cookie": "notebridge_session=" + TOKEN,
        "Origin": base,
        "X-NoteBridge-UI": "1",
        "Content-Type": "application/json",
    }
    req = Request(
        base + route,
        headers=headers,
        data=None if data is None else json.dumps(data).encode(),
    )
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def action(descriptor, operation, data=None):
    result = request(descriptor, "/api/action/" + operation, data or {})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = request(descriptor, "/api/job?id=" + result["job_id"])
        if job["status"] == "completed":
            return job["result"]
        if job["status"] == "failed":
            raise RuntimeError(job["error"])
        time.sleep(0.05)
    raise RuntimeError("验收操作未在期限内完成")


def serve(directory, port, encrypted=True):
    import faulthandler

    faulthandler.dump_traceback_later(15)
    from support.sandbox import TOKEN, Sandbox

    from wx_watcher.appstate import atomic_json

    sandbox = Sandbox(directory, encrypted=encrypted)
    server = sandbox.server(port)
    descriptor = {
        "pid": os.getpid(),
        "root": str(directory),
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "synthetic": True,
        "encrypted": encrypted,
    }
    atomic_json(directory / "environment.json", descriptor)
    faulthandler.cancel_dump_traceback_later()
    print(
        json.dumps(
            {
                **descriptor,
                "url": descriptor["base_url"] + "/#token=" + TOKEN + "&page=setup",
            }
        ),
        flush=True,
    )

    def terminate(*args):
        import threading

        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        sandbox.close()


def current():
    if not CURRENT.is_file():
        raise RuntimeError("请先运行 scripts/dev.py up")
    descriptor = json.loads(CURRENT.read_text())
    if descriptor.get("synthetic") is not True or not str(
        descriptor.get("base_url", "")
    ).startswith("http://127.0.0.1:"):
        raise ValueError("验收环境记录无效")
    return descriptor


def retain_failure_logs(directory):
    destination = ROOT / "output/checks/development-failures" / directory.name
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    for file in directory.rglob("*.log"):
        target = destination / file.relative_to(directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        with file.open("rb") as source:
            source.seek(max(0, file.stat().st_size - 128000))
            target.write_bytes(source.read())
        target.chmod(0o600)


def up(port, encrypted=True, *, register=True):
    from wx_watcher.appstate import atomic_json

    if register and CURRENT.exists():
        descriptor = current()
        try:
            request(descriptor, "/_sandbox/status")
            return {**descriptor, "reused": True}
        except (OSError, URLError):
            pass
    directory = Path(tempfile.mkdtemp(prefix="notebridge-dev-"))
    log = (directory / "server.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "serve",
            "--root",
            str(directory),
            "--port",
            str(port),
            *([] if encrypted else ["--unencrypted"]),
        ],
        cwd=ROOT,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    log.close()
    OWNED_PROCESSES[process.pid] = process
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("验收服务启动失败，请查看该临时目录中的 server.log")
            file = directory / "environment.json"
            if file.is_file():
                descriptor = json.loads(file.read_text())
                try:
                    request(descriptor, "/_sandbox/status")
                    if register:
                        atomic_json(CURRENT, descriptor)
                    return descriptor
                except OSError:
                    pass
            time.sleep(0.1)
        raise RuntimeError("验收服务启动超时")
    except BaseException:
        try:
            diagnostic = subprocess.run(
                ["/bin/ps", "-p", str(process.pid), "-o", "stat=,command="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            (directory / "startup.log").write_text(diagnostic.stdout[-2000:])
        except (OSError, subprocess.SubprocessError):
            pass
        process.terminate()
        process.wait(timeout=20)
        OWNED_PROCESSES.pop(process.pid, None)
        retain_failure_logs(directory)
        raise


def acceptance(encrypted=True):
    from test_pipeline import CHAT, GROUP

    descriptor = up(0, encrypted, register=False)
    try:
        conversations = action(descriptor, "conversations", {"query": GROUP})
        if not any(row["username"] == CHAT for row in conversations):
            raise RuntimeError("实际会话读取失败")
        action(descriptor, "settings", {"chat_id": CHAT, "group_name": GROUP})
        action(descriptor, "connect-agent")
        action(
            descriptor,
            "settings",
            {"feishu_chat_id": "oc_sandbox", "feishu_chat_name": "自动验收接收群"},
        )
        action(descriptor, "finish-setup", {"send_enabled": True})
        before = request(descriptor, "/_sandbox/status")
        request(
            descriptor, "/_sandbox/emit", {"text": "培训资料什么时候发送？自动验收问题"}
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = request(descriptor, "/_sandbox/status")
            sent = [task for task in result["tasks"] if task["status"] == "sent"]
            if sent and len(result["messages"]) == len(before["messages"]) + 1:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("新增消息到投递的流程未完成")
        action(descriptor, "stop")
        action(descriptor, "start")
        time.sleep(1)
        resumed = request(descriptor, "/_sandbox/status")
        if resumed["submissions"] != result["submissions"] or len(
            resumed["messages"]
        ) != len(result["messages"]):
            raise RuntimeError("重启后出现重复调用或投递")
        return {
            "passed": True,
            "synthetic": True,
            "encrypted_source": encrypted,
            "provider": "http",
            "conversation_read": True,
            "new_message_delivered": True,
            "restart_idempotent": True,
            "external_model_called": False,
        }
    finally:
        request(descriptor, "/_sandbox/shutdown", {})
        process = OWNED_PROCESSES.pop(descriptor["pid"], None)
        if process is not None:
            process.wait(timeout=20)


def package_acceptance(backend):
    """Reuse the production-process regression against an explicitly built backend."""
    import unittest
    from unittest.mock import patch

    backend = Path(backend).expanduser().resolve()
    if not backend.is_file() or not os.access(backend, os.X_OK):
        raise ValueError("请指定构建产物中可执行的 backend/notebridge")
    from test_full_pipeline import ProductionProcessIntegrationTests

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        ProductionProcessIntegrationTests
    )
    with patch(
        "wx_watcher.services.command", side_effect=lambda *args: [str(backend), *args]
    ):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return {
        "passed": result.wasSuccessful() and not result.skipped,
        "synthetic": True,
        "frozen_backend": True,
        "tests": result.testsRun,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "bootstrap",
            "serve",
            "up",
            "status",
            "emit",
            "down",
            "accept",
            "package-check",
        ],
    )
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--text", default="培训资料什么时候发送？")
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--unencrypted", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        result = doctor(args.native)
    elif args.command == "bootstrap":
        result = bootstrap(args.native)
    elif args.command == "serve":
        if args.root:
            serve(args.root, args.port, not args.unencrypted)
        else:
            with tempfile.TemporaryDirectory(prefix="notebridge-dev-") as tmp:
                serve(Path(tmp), args.port, not args.unencrypted)
        return
    elif args.command == "up":
        result = up(args.port, not args.unencrypted)
    elif args.command == "accept":
        result = acceptance(not args.unencrypted)
    elif args.command == "package-check":
        if args.backend is None:
            parser.error("package-check 需要 --backend 指定构建产物")
        result = package_acceptance(args.backend)
    elif args.command == "status":
        result = request(current(), "/_sandbox/status")
    elif args.command == "emit":
        result = request(current(), "/_sandbox/emit", {"text": args.text})
    else:
        result = request(current(), "/_sandbox/shutdown", {})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ready") is False or result.get("passed") is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
