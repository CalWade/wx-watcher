import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from wx_watcher.appstate import AppState, atomic_json
from wx_watcher.keyflow import KeyFlow


class KeyFlowPermissionTests(unittest.TestCase):
    def test_login_page_is_allowed_without_an_open_database_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = KeyFlow(AppState(Path(tmp) / "app"))
            with (
                patch(
                    "wx_watcher.keyflow.native_request",
                    return_value={
                        "ok": True,
                        "ready": True,
                        "database_readable": True,
                        "application_manageable": True,
                    },
                ),
                patch("wx_watcher.discovery.active_roots", return_value=[]),
            ):
                result = flow.preflight(str(Path(tmp) / "db_storage"))
                self.assertTrue(result["ready"])
                self.assertFalse(result["wechat_logged_in"])

    def test_login_confirmation_is_restricted_to_the_correct_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = KeyFlow(AppState(Path(tmp) / "app"))
            flow.jobs = Path(tmp) / "jobs"
            id = str(uuid.uuid4())
            directory = flow.jobs / id
            atomic_json(directory / "progress.json", {"phase": "signing"})
            with self.assertRaisesRegex(ValueError, "确认阶段"):
                flow.login_page_ready(id)
            self.assertFalse((directory / "login-page-ready").exists())
            atomic_json(directory / "progress.json", {"phase": "awaiting_login_page"})
            flow.login_page_ready(id)
            self.assertEqual(
                (directory / "login-page-ready").stat().st_mode & 0o777, 0o600
            )

    def test_native_denial_blocks_capture_even_when_python_can_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "db_storage"
            (root / "contact").mkdir(parents=True)
            (root / "contact/contact.db").write_bytes(b"readable to Python")
            flow = KeyFlow(AppState(Path(tmp) / "app"))
            native = {
                "ok": True,
                "ready": False,
                "database_readable": False,
                "permission_hint": "实际取钥工具缺少完整磁盘访问权限",
            }
            with (
                patch(
                    "wx_watcher.keyflow.native_request", return_value=native
                ) as request,
                patch(
                    "wx_watcher.discovery.active_roots", return_value=[root.resolve()]
                ),
                patch("wx_watcher.keyflow.subprocess.Popen") as launch,
            ):
                self.assertTrue((root / "contact/contact.db").is_file())
                with self.assertRaisesRegex(ValueError, "完整磁盘访问权限"):
                    flow.start(str(root))
                request.assert_called_once_with(
                    {"operation": "capture_preflight", "db_root": str(root.resolve())}
                )
                launch.assert_not_called()

    def test_missing_app_management_blocks_authorization_before_job_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "db_storage"
            flow = KeyFlow(AppState(Path(tmp) / "app"))
            flow.jobs = Path(tmp) / "jobs"
            with (
                patch(
                    "wx_watcher.keyflow.native_request",
                    return_value={
                        "ok": True,
                        "ready": False,
                        "database_readable": True,
                        "application_manageable": False,
                        "permission_hint": "请允许 App 管理",
                    },
                ),
                patch(
                    "wx_watcher.discovery.active_roots", return_value=[root.resolve()]
                ),
                patch("wx_watcher.keyflow.subprocess.Popen") as launch,
            ):
                with self.assertRaisesRegex(ValueError, "App 管理"):
                    flow.start(str(root))
                launch.assert_not_called()
                self.assertFalse(flow.jobs.exists())

    def test_fresh_capture_has_no_database_or_snapshot_prerequisite(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = KeyFlow(AppState(Path(tmp) / "app"))
            flow.jobs = Path(tmp) / "jobs"
            with (
                patch(
                    "wx_watcher.keyflow.native_request",
                    return_value={
                        "ready": True,
                        "data_access_ready": True,
                        "database_readable": False,
                        "application_manageable": True,
                    },
                ),
                patch("wx_watcher.discovery.active_roots", return_value=[]),
                patch(
                    "wx_watcher.keyflow.native_tool",
                    return_value=Path("/synthetic/helper"),
                ),
                patch("wx_watcher.keyflow.subprocess.Popen") as launch,
                patch("wx_watcher.keyflow.threading.Thread"),
            ):
                result = flow.start(discover_account=True)
            directory = flow.directory(result["capture_id"])
            request = json.loads((directory / "request.json").read_text())
            self.assertEqual(request["db_root"], "")
            self.assertEqual(request["account_mode"], "login")
            self.assertFalse((directory / "samples").exists())
            self.assertFalse((directory / "pending-key").exists())
            self.assertEqual(launch.call_count, 1)
            self.assertIn("with administrator privileges", launch.call_args.args[0][-1])

    def test_captured_login_requires_matching_canonical_account_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            root = (
                home
                / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/new/db_storage"
            )
            flow = KeyFlow(Mock())
            flow.jobs = home / "jobs"
            id = str(uuid.uuid4())
            directory = flow.jobs / id
            atomic_json(
                directory / "request.json", {"account_mode": "login", "db_root": ""}
            )
            atomic_json(
                directory / "progress.json",
                {"phase": "captured", "official_restored": True},
            )
            (directory / "pending-key").write_text("ab" * 32)
            (directory / "pending-key").chmod(0o600)
            with (
                patch("wx_watcher.keyflow.Path.home", return_value=home),
                patch(
                    "wx_watcher.keyflow.native_request",
                    return_value={"key_ref": "synthetic", "verified": 1, "total": 1},
                ) as store,
            ):
                with self.assertRaisesRegex(ValueError, "尚未确认"):
                    flow.status(id)
                atomic_json(
                    directory / "verified-account.json",
                    {"db_root": str(home / "other/db_storage")},
                )
                with self.assertRaisesRegex(ValueError, "目录无效"):
                    flow.status(id)
                store.assert_not_called()
                flow.state.add_profile.assert_not_called()
                atomic_json(directory / "verified-account.json", {"db_root": str(root)})
                atomic_json(
                    directory / "request.json",
                    {"db_root": str(root).replace("/new/", "/old/")},
                )
                with self.assertRaisesRegex(ValueError, "不一致"):
                    flow.status(id)
                store.assert_not_called()
                atomic_json(
                    directory / "request.json", {"account_mode": "login", "db_root": ""}
                )
                result = flow.status(id)
            self.assertEqual(result["phase"], "completed")
            self.assertEqual(result["account_label"], "new")
            self.assertEqual(store.call_args.args[0]["db_root"], str(root))
            self.assertEqual(flow.state.add_profile.call_args.args[0], root)
            self.assertFalse((directory / "pending-key").exists())
