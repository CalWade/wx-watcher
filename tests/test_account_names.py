import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wx_watcher.account_names import read_identity
from wx_watcher.appstate import AppState
from wx_watcher.config import Config
from wx_watcher.controller import Controller
from wx_watcher.source import SnapshotReader


class AccountNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / "wxid_demoowner_abcd/db_storage"
        (self.root / "contact").mkdir(parents=True)
        self.db = self.root / "contact/contact.db"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,alias TEXT)")
            c.executemany(
                "INSERT INTO contact VALUES(?,?,?)",
                [
                    ("wxid_someoneelse", "别人的昵称", "wrong-person"),
                    ("wxid_demoowner", "工作昵称", "my-wechat-id"),
                ],
            )
        self.state = AppState(self.home / "app")

    def tearDown(self):
        self.temp.cleanup()

    def reader(self):
        return SnapshotReader(
            Config(
                db_root=self.root,
                key_file=self.home / "unused",
                state_dir=self.home / "preview",
                backend="sqlite",
            )
        )

    def test_only_self_contact_can_name_the_account_and_source_is_unchanged(self):
        before = self.db.read_bytes()
        self.assertEqual(
            read_identity(self.reader(), self.root),
            {"nickname": "工作昵称", "wechat_id": "my-wechat-id"},
        )
        self.assertEqual(self.db.read_bytes(), before)
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM contact WHERE username='wxid_demoowner'")
        self.assertEqual(read_identity(self.reader(), self.root), {})

    def test_ambiguous_self_rows_do_not_pick_another_person(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO contact VALUES(?,?,?)",
                (self.root.parent.name, "冲突昵称", "conflict"),
            )
        self.assertEqual(read_identity(self.reader(), self.root), {})

    def test_names_persist_before_capture_and_survive_recapture(self):
        self.state.save_account_names(self.root, custom_label="客户服务号 👩‍💻")
        self.state.add_profile(self.root, "synthetic-key-one")
        self.assertEqual(self.state.profile()["label"], "客户服务号 👩‍💻")
        self.state.save_account_names(
            self.root, nickname="新昵称", wechat_id="work-user"
        )
        self.state.add_profile(self.root, "synthetic-key-two")
        self.assertEqual(self.state.profile()["label"], "客户服务号 👩‍💻")
        result = self.state.save_account_names(self.root, custom_label="")
        self.assertEqual(result["label"], "新昵称 · work-user")
        self.assertEqual(
            AppState(self.home / "app").profile()["label"], result["label"]
        )

    def test_metadata_refresh_preserves_legacy_manual_label(self):
        self.state.add_profile(self.root, "synthetic-key")
        settings = self.state.load()
        settings["profiles"][0]["label"] = "原有的备注"
        self.state.update(profiles=settings["profiles"])
        self.state.save_account_names(
            self.root, nickname="微信昵称", wechat_id="wx-custom"
        )
        self.assertEqual(self.state.profile()["label"], "原有的备注")

    def test_renaming_does_not_switch_account_reset_scope_or_access_credentials(self):
        c = Controller(self.home / "controller")
        c.services.shutdown_event.set()
        try:
            c.state.add_profile(self.root, "synthetic-key")
            c.state.update(chat_id="room", send_enabled=True, autostart_monitor=True)
            before = c.state.load()
            c.accounts = Mock(
                return_value=[
                    {
                        "db_root": str(self.root),
                        "label": self.root.parent.name,
                        "active": True,
                    }
                ]
            )
            with (
                patch("wx_watcher.controller.native_request") as keychain,
                patch.object(c.services, "stop") as stop,
            ):
                c.perform(
                    "account-name", {"db_root": str(self.root), "label": "我的工作号"}
                )
                report = c.account_status()
            keychain.assert_not_called()
            stop.assert_not_called()
            after = c.state.load()
            for name in [
                "active_profile",
                "chat_id",
                "send_enabled",
                "autostart_monitor",
            ]:
                self.assertEqual(before[name], after[name])
            for name in ["id", "db_root", "key_ref"]:
                self.assertEqual(
                    before["profiles"][0][name], after["profiles"][0][name]
                )
            self.assertEqual(report["current"]["label"], "我的工作号")
            self.assertNotIn("synthetic-key", json.dumps(report))
            with self.assertRaisesRegex(ValueError, "已识别"):
                c.perform(
                    "account-name",
                    {"db_root": "/unrelated/db_storage", "label": "wrong"},
                )
        finally:
            c.pool.shutdown(wait=True)

    def test_invalid_names_are_rejected_without_changing_settings(self):
        before = self.state.load()
        for label in ["x" * 81, "a\nb", "a\x00b", {"bad": "value"}]:
            with self.assertRaises(ValueError):
                self.state.save_account_names(self.root, custom_label=label)
        self.assertEqual(before, self.state.load())
