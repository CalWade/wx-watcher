import json
import unittest
from unittest.mock import patch

from wx_watcher.feishu import Feishu


class FeishuTests(unittest.TestCase):
    def test_cli_unwrapped_text_is_compared_exactly(self):
        client = Feishu("cli")
        result = {
            "data": {
                "messages": [
                    {
                        "message_id": "m1",
                        "msg_type": "text",
                        "content": "line one\n第二行",
                    }
                ]
            }
        }
        with patch.object(client, "readback", return_value=result):
            self.assertTrue(client.confirm_readback("m1", "line one\n第二行"))
            with self.assertRaises(RuntimeError):
                client.confirm_readback("m1", "different")
            with self.assertRaises(RuntimeError):
                client.confirm_readback("wrong-id", "line one\n第二行")

    def test_direct_bot_send_preserves_idempotency_and_plain_text(self):
        client = Feishu("openapi", "app", "reference")
        with patch.object(
            client, "_http", return_value={"code": 0, "data": {"message_id": "m1"}}
        ) as http:
            result = client.send(
                "oc_test", "literal ![image](https://invalid/image)", "unique-key"
            )
        route, body = http.call_args.args
        self.assertEqual(route, "im/v1/messages?receive_id_type=chat_id")
        self.assertEqual(body["msg_type"], "text")
        self.assertEqual(body["uuid"], "unique-key")
        self.assertEqual(
            json.loads(body["content"])["text"],
            "literal ![image](https://invalid/image)",
        )
        self.assertTrue(result["ok"])

    def test_dry_run_does_not_request_token_or_network(self):
        with patch.object(Feishu, "_http") as http:
            result = Feishu("openapi").send("oc_test", "example", "id", True)
        self.assertTrue(result["dry_run"])
        http.assert_not_called()

    def test_chat_pagination(self):
        client = Feishu("openapi")
        with patch.object(
            client,
            "_http",
            side_effect=[
                {
                    "data": {
                        "items": [{"chat_id": "a"}],
                        "has_more": True,
                        "page_token": "next",
                    }
                },
                {"data": {"items": [{"chat_id": "b"}], "has_more": False}},
            ],
        ) as http:
            result = client.chats()
        self.assertEqual(len(result["data"]["chats"]), 2)
        self.assertIn("page_token=next", http.call_args.args[0])

    def test_readback_checks_message_identity_and_content(self):
        client = Feishu("openapi")
        with patch.object(
            client,
            "readback",
            return_value={
                "data": {
                    "messages": [
                        {
                            "message_id": "different",
                            "content": json.dumps({"text": "expected"}),
                        }
                    ]
                }
            },
        ):
            with self.assertRaises(RuntimeError):
                client.confirm_readback("m1", "expected")
        with patch.object(
            client,
            "readback",
            return_value={
                "data": {
                    "messages": [
                        {
                            "message_id": "m1",
                            "body": {"content": json.dumps({"text": "expected"})},
                        }
                    ]
                }
            },
        ):
            self.assertTrue(client.confirm_readback("m1", "expected"))
        with patch.object(client, "readback", return_value={"data": {"messages": []}}):
            with self.assertRaises(RuntimeError):
                client.confirm_readback("m1", "expected")
