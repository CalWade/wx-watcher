"""Authenticated loopback JSON client."""

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class LocalAPI:
    def __init__(self, port: int, token_file: Path):
        self.base = f"http://127.0.0.1:{port}"
        if token_file.stat().st_mode & 0o077:
            raise PermissionError("Local API token must have mode 600")
        self.token = token_file.read_text().strip()

    def request(self, route: str, data=None):
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode()
        req = Request(
            self.base + route,
            data=raw,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            # HTTP bodies can contain customer text; report only a bounded status.
            if exc.code == 410:
                raise RuntimeError(
                    "事件游标已超过保留期；请在诊断中确认历史缺口后重建当前会话基线"
                ) from None
            raise RuntimeError(f"Local service returned HTTP {exc.code}") from None
