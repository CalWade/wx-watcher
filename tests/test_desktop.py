import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wx_watcher.appstate import AppState
from wx_watcher.controller import Controller
from wx_watcher.credentials import native_request
from wx_watcher.desktop import create_desktop
from wx_watcher.knowledge import select_passages


class DesktopSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.temp.name) / "app")
        self.server = create_desktop(self.controller, "test-session-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.controller.pool.shutdown(wait=True)
        self.controller.services.shutdown_event.set()
        self.temp.cleanup()

    def call(self, route, data=None, authorized=True, origin=None):
        headers = {"Cookie": "wx_session=test-session-token"} if authorized else {}
        if data is not None:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "X-WX-UI": "1",
                    "Origin": origin or self.base,
                }
            )
        req = Request(
            self.base + route,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
        )
        with urlopen(req) as r:
            return r.status, r.headers, json.load(r)

    def test_settings_requires_authentication(self):
        with self.assertRaises(HTTPError) as error:
            self.call("/api/settings", authorized=False)
        self.assertEqual(error.exception.code, 401)

    def test_cross_origin_write_rejected_even_with_cookie(self):
        with self.assertRaises(HTTPError) as error:
            self.call(
                "/api/action/settings",
                {"send_enabled": True},
                origin="https://untrusted.invalid",
            )
        self.assertEqual(error.exception.code, 403)
        self.assertFalse(self.controller.state.load()["send_enabled"])

    def test_auth_cookie_is_http_only_and_same_site(self):
        code, headers, _ = self.call(
            "/api/auth", {"token": "test-session-token"}, authorized=False
        )
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("notebridge_session=", headers["Set-Cookie"])

    def test_branded_header_and_cookie_are_accepted(self):
        request = Request(
            self.base + "/api/auth",
            data=json.dumps({"token": "test-session-token"}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-NoteBridge-UI": "1",
                "Origin": self.base,
            },
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)
        request = Request(
            self.base + "/api/settings",
            headers={"Cookie": "notebridge_session=test-session-token"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)

    def test_no_raw_secret_or_arbitrary_command_endpoint(self):
        for endpoint in ["read", "keychain-read", "execute", "sql"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(HTTPError) as error:
                self.call("/api/action/" + endpoint, {})
            self.assertEqual(error.exception.code, 404)

    def test_rebinding_host_rejected(self):
        req = Request(
            self.base + "/api/settings",
            headers={
                "Host": "untrusted.invalid",
                "Cookie": "wx_session=test-session-token",
            },
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(req)
        self.assertEqual(error.exception.code, 403)

    def test_settings_ignores_secret_fields(self):
        self.controller.perform(
            "settings",
            {
                "key_hex": "do-not-save",
                "feishu_secret_ref": "arbitrary",
                "send_enabled": False,
            },
        )
        self.assertNotIn("key_hex", self.controller.state.load())
        self.assertEqual(self.controller.state.load()["feishu_secret_ref"], "")

    def test_state_rejects_protected_or_root_directory(self):
        for path in [
            Path("/"),
            Path.home(),
            Path.home() / "Library/Containers/com.tencent.xinWeChat/unsafe",
        ]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                AppState(path)

    def test_config_file_private(self):
        self.assertEqual(self.controller.state.file.stat().st_mode & 0o777, 0o600)


class KnowledgeTests(unittest.TestCase):
    def test_selection_is_bounded_and_records_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "faq.md"
            a.write_text(
                "普通介绍\n\n培训时间：周五上午十点。\n\n" + ("无关内容\n\n" * 1000)
            )
            result, sources = select_passages((a,), "培训时间", budget=150)
            self.assertIn("周五上午十点", result)
            self.assertLessEqual(len(result), 170)
            self.assertEqual(sources[0]["name"], "faq.md")
            self.assertEqual(len(sources[0]["sha256"]), 64)

    def test_no_symlink_or_binary_knowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "source.txt"
            a.write_text("example")
            b = Path(tmp) / "link.txt"
            b.symlink_to(a)
            with self.assertRaises(ValueError):
                select_passages((b,), "example")
            with self.assertRaises(ValueError):
                select_passages((Path(tmp) / "file.db",), "example")


class CredentialDiagnosticTests(unittest.TestCase):
    def test_denied_data_access_guides_permission_instead_of_recapture(self):
        with (
            patch(
                "wx_watcher.credentials.native_tool",
                return_value=Path("/example/notebridge-access"),
            ),
            patch(
                "wx_watcher.credentials.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=2, stdout='{"ok":false,"error":"database_access_denied"}'
                ),
            ),
        ):
            with self.assertRaises(PermissionError) as error:
                native_request({"operation": "verify"})
        self.assertIn("完整磁盘访问权限", str(error.exception))
        self.assertIn("无需重新获取", str(error.exception))
