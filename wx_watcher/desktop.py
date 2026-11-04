"""Authenticated local control panel and native-app backend."""
from __future__ import annotations
import argparse
import fcntl
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .appstate import app_home, atomic_json
from .controller import Controller


def create_desktop(controller: Controller, token: str, port=0):
    assets = Path(__file__).parent/"assets"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def host_ok(self): return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
        def authorized(self):
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie.get("wx_session")
                return self.host_ok() and value is not None and hmac.compare_digest(value.value, token)
            except Exception: return False
        def send(self, status, value, content_type="application/json; charset=utf-8", cookie=False):
            raw = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith("application/json") else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie: self.send_header("Set-Cookie", f"wx_session={token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            try: self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError): pass
        def do_GET(self):
            if not self.host_ok(): return self.send(403, {"error": "host_not_allowed"})
            route = urlsplit(self.path)
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if route.path in static:
                name, kind = static[route.path]
                return self.send(200, (assets/name).read_bytes(), kind)
            if not self.authorized(): return self.send(401, {"error": "请从本地应用或受保护的启动入口打开"})
            try:
                if route.path == "/api/settings": return self.send(200, controller.state.load())
                if route.path == "/api/status":
                    return self.send(200, {"version": "0.2.0", "services": controller.services.status(),
                        "records": controller.records(), "jobs": list(controller.jobs.values())[-20:]})
                if route.path == "/api/job":
                    id = parse_qs(route.query).get("id", [""])[0]
                    job = controller.jobs.get(id)
                    return self.send(200 if job else 404, job or {"error": "job_not_found"})
                return self.send(404, {"error": "not_found"})
            except Exception:
                return self.send(503, {"error": "状态读取失败，请运行环境检查"})
        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.host_ok() or origin != f"http://127.0.0.1:{self.server.server_port}" or self.headers.get("X-WX-UI") != "1":
                return self.send(403, {"error": "origin_not_allowed"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_048_576: raise ValueError
                if self.headers.get_content_type() != "application/json": raise ValueError
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict): raise ValueError
            except (ValueError, json.JSONDecodeError):
                return self.send(400, {"error": "invalid_request"})
            if self.path == "/api/auth":
                candidate = data.get("token", "")
                if isinstance(candidate, str) and hmac.compare_digest(candidate, token):
                    return self.send(200, {"ok": True}, cookie=True)
                return self.send(401, {"error": "authentication_failed"})
            if not self.authorized(): return self.send(401, {"error": "authentication_required"})
            allowed = {"doctor", "accounts", "conversations", "import-key", "verify-key", "backup-key", "restore-key",
                "settings", "feishu-secret", "feishu-chats", "test-feishu", "test-dsh", "install-dsh", "start", "stop",
                "capture-preflight", "capture-start", "capture-status", "capture-cancel", "capture-sessions", "capture-recover", "diagnostics"}
            operation = self.path.removeprefix("/api/action/")
            if not self.path.startswith("/api/action/") or operation not in allowed:
                return self.send(404, {"error": "unknown_operation"})
            return self.send(202, controller.submit(operation, data))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    import sys
    parser = argparse.ArgumentParser(description="WX Watcher 本地应用")
    parser.add_argument("command", choices=["desktop", "setup", "service", "keytool"])
    parser.add_argument("action", nargs="?", choices=["install", "uninstall", "status"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--keytool", action="store_true")
    args = parser.parse_args()
    directory = (args.data_dir or app_home()).expanduser().resolve()
    if args.command == "service":
        return manage_login(args.action or "status", directory)
    controller = Controller(directory)
    lock = (directory/"desktop.lock").open("a+")
    os.chmod(lock.name, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        endpoint = json.loads((directory/"desktop-endpoint.json").read_text())
        print(json.dumps({"desktop_url": endpoint["url"]}), flush=True)
        if not args.no_browser: webbrowser.open(endpoint["url"])
        return 0
    token = secrets.token_urlsafe(32)
    server = create_desktop(controller, token, args.port)
    fragment = "keytool" if args.keytool or args.command == "keytool" else "setup"
    url = f"http://127.0.0.1:{server.server_port}/#token={token}&page={fragment}"
    atomic_json(directory/"desktop-endpoint.json", {"url": url, "pid": os.getpid()})
    print(json.dumps({"desktop_url": url}), flush=True)  # Private pipe to the native launcher.
    if not args.no_browser: webbrowser.open(url)
    def stop(*_): threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if controller.state.load().get("autostart_monitor"):
        controller.submit("start", {})
    try:
        server.serve_forever()
    finally:
        from .keyflow import KeyFlow
        KeyFlow(controller.state).cancel_owned()
        controller.services.stop()
        controller.pool.shutdown(wait=False, cancel_futures=True)
        server.server_close()
        lock.close()
    return 0


def manage_login(action, directory):
    import plistlib
    import subprocess
    from .services import command
    file = Path.home()/"Library/LaunchAgents/io.github.calwade.wx-watcher.plist"
    if action == "install":
        file.parent.mkdir(parents=True, exist_ok=True)
        value = {"Label": "io.github.calwade.wx-watcher", "ProgramArguments": command("desktop", "--no-browser", "--data-dir", str(directory)),
            "RunAtLoad": True, "KeepAlive": False, "WorkingDirectory": str(Path.home()),
            "StandardOutPath": str(directory/"desktop-launch.log"), "StandardErrorPath": str(directory/"desktop-error.log")}
        with file.open("wb") as f: plistlib.dump(value, f)
        file.chmod(0o600)
        print(json.dumps({"installed": True, "starts_at_next_login": True}))
    elif action == "uninstall":
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(file)], capture_output=True)
        file.unlink(missing_ok=True)
        print(json.dumps({"installed": False}))
    else: print(json.dumps({"installed": file.exists()}))
    return 0
