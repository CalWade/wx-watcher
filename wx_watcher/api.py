"""只提供固定的 GET 资源；没有 SQL、发信、执行命令或读取任意文件的入口。"""

from __future__ import annotations

import hmac
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .collector import Collector
from .source import private_dir
from .store import CursorExpired, Store


def load_token(directory: Path) -> str:
    private_dir(directory)
    path = directory / "api-token.txt"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.stat().st_mode & 0o077:
            raise PermissionError("API token 文件必须仅本人可读写（600）")
        token = path.read_text().strip()
        if len(token) < 32:
            raise ValueError("API token 文件无效")
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as out:
        out.write(token + "\n")
    return token


def create_server(
    store: Store, collector: Collector, token: str, port: int
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # 不记录认证头或客户消息正文。

        def reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            expected = "Bearer " + token
            if not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), expected.encode()
            ):
                return self.reply(401, {"error": "需要 Bearer token"})
            route = urlsplit(self.path)
            params = parse_qs(route.query)
            try:

                def integer(name, default, minimum=0, maximum=2000):
                    value = params.get(name, [str(default)])
                    if len(value) != 1:
                        raise ValueError("重复参数")
                    number = int(value[0])
                    if not minimum <= number <= maximum:
                        raise ValueError(f"{name} 超出范围")
                    return number

                if route.path == "/v1/status":
                    return self.reply(200, collector.status())
                if route.path == "/v1/messages":
                    if collector.status()["state"] != "ready":
                        return self.reply(
                            503, {"error": "数据源尚未就绪，请查看 /v1/status"}
                        )
                    data = {
                        "group_name": collector.status().get(
                            "group_name", collector.config.group_name
                        ),
                        "messages": store.history(integer("limit", 50, 1, 200)),
                    }
                    return self.reply(200, data)
                if route.path == "/v1/events":
                    if collector.status()["state"] != "ready":
                        return self.reply(
                            503, {"error": "数据源尚未就绪，请查看 /v1/status"}
                        )
                    return self.reply(
                        200,
                        store.events(
                            integer("after", 0, 0, 2**63 - 1),
                            integer("limit", 200, 1, 1000),
                        ),
                    )
                return self.reply(404, {"error": "资源不存在"})
            except CursorExpired as exc:
                return self.reply(410, {"error": "cursor_expired", "message": str(exc)})
            except ValueError as exc:
                return self.reply(400, {"error": str(exc)})

        def deny(self):
            self.reply(405, {"error": "只读接口，仅支持 GET"})

        do_POST = do_PUT = do_PATCH = do_DELETE = deny

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
