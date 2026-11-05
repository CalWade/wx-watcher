"""Authenticated local control panel and native-app backend."""

from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import os
import secrets
import signal
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .appstate import AppState, app_home, atomic_json
from .branding import BUNDLE_ID, LEGACY_BUNDLE_ID, login_file
from .controller import Controller


def create_desktop(controller: Controller, token: str, port=0):
    assets = Path(__file__).parent / "assets"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def host_ok(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authorized(self):
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie.get("notebridge_session") or cookie.get("wx_session")
                return (
                    self.host_ok()
                    and value is not None
                    and hmac.compare_digest(value.value, token)
                )
            except Exception:
                return False

        def send(
            self,
            status,
            value,
            content_type="application/json; charset=utf-8",
            cookie=False,
        ):
            raw = (
                json.dumps(value, ensure_ascii=False).encode()
                if content_type.startswith("application/json")
                else value
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"notebridge_session={token}; HttpOnly; SameSite=Strict; Path=/",
                )
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "host_not_allowed"})
            route = urlsplit(self.path)
            static = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            if route.path in static:
                name, kind = static[route.path]
                return self.send(200, (assets / name).read_bytes(), kind)
            if not self.authorized():
                return self.send(401, {"error": "请从本地应用或受保护的启动入口打开"})
            try:
                if route.path == "/api/settings":
                    return self.send(200, controller.state.load())
                if route.path == "/api/status":
                    return self.send(
                        200,
                        {
                            "version": "0.4.3",
                            "services": controller.services.status(),
                            "records": controller.records(),
                            "jobs": list(controller.jobs.values())[-20:],
                            "login_installed": login_file().exists(),
                        },
                    )
                if route.path == "/api/job":
                    id = parse_qs(route.query).get("id", [""])[0]
                    job = controller.jobs.get(id)
                    return self.send(
                        200 if job else 404, job or {"error": "job_not_found"}
                    )
                return self.send(404, {"error": "not_found"})
            except Exception:
                return self.send(503, {"error": "状态读取失败，请运行环境检查"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (
                not self.host_ok()
                or origin != f"http://127.0.0.1:{self.server.server_port}"
                or (self.headers.get("X-NoteBridge-UI") or self.headers.get("X-WX-UI"))
                != "1"
            ):
                return self.send(403, {"error": "origin_not_allowed"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_048_576:
                    raise ValueError
                if self.headers.get_content_type() != "application/json":
                    raise ValueError
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                return self.send(400, {"error": "invalid_request"})
            if self.path == "/api/auth":
                candidate = data.get("token", "")
                if isinstance(candidate, str) and hmac.compare_digest(candidate, token):
                    return self.send(200, {"ok": True}, cookie=True)
                return self.send(401, {"error": "authentication_failed"})
            if not self.authorized():
                return self.send(401, {"error": "authentication_required"})
            allowed = {
                "doctor",
                "accounts",
                "account-status",
                "conversations",
                "import-key",
                "verify-key",
                "backup-key",
                "restore-key",
                "settings",
                "feishu-secret",
                "feishu-chats",
                "test-feishu",
                "test-dsh",
                "install-dsh",
                "start",
                "stop",
                "capture-preflight",
                "connection-check",
                "capture-start",
                "capture-status",
                "capture-cancel",
                "capture-login-ready",
                "capture-sessions",
                "capture-recover",
                "diagnostics",
                "task-review",
                "reset-cursor",
                "login-service",
                "auto-connect",
                "connect-dsh",
                "finish-setup",
                "run-settings",
            }
            operation = self.path.removeprefix("/api/action/")
            if not self.path.startswith("/api/action/") or operation not in allowed:
                return self.send(404, {"error": "unknown_operation"})
            return self.send(202, controller.submit(operation, data))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="NoteBridge 本地应用")
    parser.add_argument(
        "command", choices=["desktop", "setup", "service", "credentials", "keytool"]
    )
    parser.add_argument("action", nargs="?", choices=["install", "uninstall", "status"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--credentials", "--keytool", dest="keytool", action="store_true"
    )
    args = parser.parse_args()
    migration = {}
    if args.data_dir is None and not (
        os.environ.get("NOTE_BRIDGE_HOME") or os.environ.get("WX_WATCHER_HOME")
    ):
        from .migration import migrate_default_state

        try:
            migration = migrate_default_state()
        except (OSError, RuntimeError, ValueError) as error:
            print(
                json.dumps({"desktop_error": str(error)}, ensure_ascii=False),
                flush=True,
            )
            return 1
    directory = (args.data_dir or app_home()).expanduser().resolve()
    if args.command == "service":
        return manage_login(args.action or "status", directory)
    controller = Controller(directory)
    lock = (directory / "desktop.lock").open("a+")
    os.chmod(lock.name, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        endpoint = json.loads((directory / "desktop-endpoint.json").read_text())
        url = endpoint["url"]
        if args.keytool or args.command in {"credentials", "keytool"}:
            url = url.replace("page=setup", "page=keytool")
        print(json.dumps({"desktop_url": url, "reused": True}), flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    token = secrets.token_urlsafe(32)
    server = create_desktop(controller, token, args.port)
    fragment = (
        "keytool"
        if args.keytool or args.command in {"credentials", "keytool"}
        else "setup"
    )
    url = f"http://127.0.0.1:{server.server_port}/#token={token}&page={fragment}"
    atomic_json(directory / "desktop-endpoint.json", {"url": url, "pid": os.getpid()})
    print(
        json.dumps({"desktop_url": url}), flush=True
    )  # Private pipe to the native launcher.
    if not args.no_browser:
        webbrowser.open(url)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    parent = os.environ.get("NOTE_BRIDGE_PARENT_PID") or os.environ.get(
        "WX_WATCHER_PARENT_PID", ""
    )
    parent_done = threading.Event()
    if parent.isdecimal() and int(parent) == os.getppid():

        def watch_parent():
            while not parent_done.wait(2):
                if os.getppid() != int(parent):
                    server.shutdown()
                    break

        threading.Thread(
            target=watch_parent, daemon=True, name="notebridge-owner"
        ).start()
    if migration.get("legacy_login_installed"):
        manage_login("install", directory, quiet=True)
    if controller.state.load().get("autostart_monitor") or controller.state.load().get(
        "migration_resume_requested"
    ):
        controller.submit("start", {})
    try:
        server.serve_forever()
    finally:
        parent_done.set()
        from .keyflow import KeyFlow

        KeyFlow(controller.state).cancel_owned()
        controller.services.stop()
        controller.services.shutdown_event.set()
        controller.pool.shutdown(wait=False, cancel_futures=True)
        server.server_close()
        lock.close()
    return 0


def manage_login(action, directory, quiet=False):
    import plistlib
    import subprocess
    import sys

    from .services import command

    file = login_file()
    legacy = file.with_name(LEGACY_BUNDLE_ID + ".plist")
    if action in {"install", "uninstall"} and legacy.exists():
        subprocess.run(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(legacy)],
            capture_output=True,
        )
        legacy.unlink()
    if action == "install":
        directory = AppState(directory).home
        file.parent.mkdir(parents=True, exist_ok=True)
        args = command("desktop", "--no-browser", "--data-dir", str(directory))
        if getattr(sys, "frozen", False):
            gui = Path(sys.executable).parents[2] / "MacOS/NoteBridge"
            if gui.is_file():
                args = [str(gui), "--background"]
        for name in ["desktop-launch.log", "desktop-error.log"]:
            log = directory / name
            log.touch(mode=0o600, exist_ok=True)
            log.chmod(0o600)
        value = {
            "Label": BUNDLE_ID,
            "AssociatedBundleIdentifiers": [BUNDLE_ID],
            "ProgramArguments": args,
            "EnvironmentVariables": {"NOTE_BRIDGE_HOME": str(directory)},
            "RunAtLoad": True,
            "KeepAlive": False,
            "WorkingDirectory": str(Path.home()),
            "StandardOutPath": str(directory / "desktop-launch.log"),
            "StandardErrorPath": str(directory / "desktop-error.log"),
        }
        with file.open("wb") as f:
            plistlib.dump(value, f)
        file.chmod(0o600)
        result = {"installed": True, "starts_at_next_login": True}
    elif action == "uninstall":
        subprocess.run(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(file)],
            capture_output=True,
        )
        file.unlink(missing_ok=True)
        result = {"installed": False}
    elif action == "status":
        result = {"installed": file.exists()}
    else:
        raise ValueError("登录启动选项无效")
    if quiet:
        return result
    print(json.dumps(result))
    return 0
