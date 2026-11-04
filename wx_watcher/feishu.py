"""Feishu application-bot transport, with existing lark-cli compatibility."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class Feishu:
    def __init__(self, transport="cli", app_id="", secret_ref="", cli=""):
        self.transport, self.app_id, self.secret_ref = transport, app_id, secret_ref
        self.cli = cli or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"
        self.token = None
        self.expires_at = 0
        self.lock = threading.Lock()

    def _cli(self, args):
        result = subprocess.run(
            [self.cli, "im", *args, "--as", "bot", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
                "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
            },
        )
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("飞书 CLI 调用失败，请检查应用配置及授权") from None
        if result.returncode or not data.get("ok"):
            raise RuntimeError("飞书调用失败，请检查机器人权限和群成员关系")
        return data

    def _http(self, route, data=None, *, auth=True):
        raw = (
            json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        )
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = "Bearer " + self._access_token()
        try:
            with urlopen(
                Request(
                    "https://open.feishu.cn/open-apis/" + route,
                    data=raw,
                    headers=headers,
                ),
                timeout=20,
            ) as response:
                value = json.load(response)
        except Exception:
            raise RuntimeError("飞书网络请求未确认成功，请检查网络与任务状态") from None
        if value.get("code", 0) != 0:
            raise RuntimeError("飞书 API 拒绝请求，错误码 " + str(value.get("code")))
        return value

    def _access_token(self):
        with self.lock:
            if self.token and time.monotonic() < self.expires_at:
                return self.token
            from .credentials import native_request

            secret = native_request(
                {"operation": "read_secret", "key_ref": self.secret_ref},
                internal_read=True,
            )["secret"]
            result = self._http(
                "auth/v3/tenant_access_token/internal",
                {"app_id": self.app_id, "app_secret": secret},
                auth=False,
            )
            self.token = result["tenant_access_token"]
            self.expires_at = time.monotonic() + max(
                0, int(result.get("expire", 7200)) - 120
            )
            return self.token

    def chats(self):
        if self.transport == "cli":
            return self._cli(["+chat-list", "--page-size", "100"])
        items, token = [], ""
        for _ in range(20):
            result = self._http(
                "im/v1/chats?" + urlencode({"page_size": 100, "page_token": token})
            )["data"]
            items.extend(result.get("items", []))
            if not result.get("has_more"):
                return {
                    "ok": True,
                    "identity": "bot",
                    "data": {"chats": items, "has_more": False},
                }
            token = result.get("page_token", "")
        raise RuntimeError("飞书群列表超出上限，请缩小机器人加入范围")

    def send(self, chat_id, text, idempotency_key, dry_run=False):
        if self.transport == "cli":
            return self._cli(
                [
                    "+messages-send",
                    "--chat-id",
                    chat_id,
                    "--msg-type",
                    "text",
                    "--content",
                    json.dumps({"text": text}, ensure_ascii=False),
                    "--idempotency-key",
                    idempotency_key,
                    *(["--dry-run"] if dry_run else []),
                ]
            )
        if dry_run:
            return {
                "ok": True,
                "identity": "bot",
                "dry_run": True,
                "data": {"chat_id": chat_id, "text": text},
            }
        result = self._http(
            "im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "uuid": idempotency_key,
            },
        )
        return {"ok": True, "identity": "bot", "data": result["data"]}

    def readback(self, message_id):
        if self.transport == "cli":
            return self._cli(
                ["+messages-mget", "--message-ids", message_id, "--no-reactions"]
            )
        result = self._http("im/v1/messages/" + message_id)
        return {"ok": True, "data": {"messages": result["data"].get("items", [])}}

    def confirm_readback(self, message_id, expected_text):
        result = self.readback(message_id)
        for message in result.get("data", {}).get("messages", []):
            if message.get("message_id") != message_id or message.get("deleted"):
                continue
            content = message.get("content", message.get("body", {}).get("content", ""))
            try:
                text = json.loads(content).get("text")
            except (ValueError, TypeError, AttributeError):
                text = None
            if text == expected_text:
                return True
        raise RuntimeError(
            "测试消息已提交，但回读内容未确认一致；请先在飞书群中核对，不要重复点击发送"
        )
