import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wx_watcher.appstate import AppState, atomic_json
from wx_watcher.branding import app_home, dsh_storage
from wx_watcher.controller import Controller
from wx_watcher.migration import migrate_default_state, migrate_dsh_storage
from wx_watcher.services import command


class BrandingMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()
        self.legacy = self.home / "Library/Application Support/WXWatcher"
        self.primary = self.home / "Library/Application Support/NoteBridge"
        state = AppState(self.legacy)
        root = self.home / "account/db_storage"
        (root / "contact").mkdir(parents=True)
        (root / "contact/contact.db").write_bytes(b"synthetic header")
        state.update(
            profiles=[
                {
                    "id": "stable-profile",
                    "db_root": str(root),
                    "key_ref": "unchanged-reference",
                }
            ],
            active_profile="stable-profile",
            chat_id="filehelper",
            group_name="fixture",
            knowledge_files=[str(self.legacy / "knowledge/faq.md")],
            business_context="Keep this literal unchanged: " + str(self.legacy),
            feishu_secret_ref="unchanged-secret-reference",
        )
        state.write_configs()
        self.dbfile = (
            self.legacy / "profiles/stable-profile/filehelper/reader/events.sqlite3"
        )
        self.dbfile.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.dbfile) as db:
            db.execute("CREATE TABLE fixture (cursor INTEGER, content TEXT)")
            db.execute("INSERT INTO fixture VALUES (42, 'WXWatcher is user content')")

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_preserves_identity_cursor_content_and_compatibility_paths(self):
        original = json.loads((self.legacy / "settings.json").read_text())
        atomic_json(self.legacy / "migration-request.json", {"resume_monitor": True})
        atomic_json(
            self.legacy / "keytool/jobs/fixture/progress.json", {"phase": "completed"}
        )
        report = migrate_default_state(self.home)
        self.assertTrue(report["migrated"])
        self.assertEqual(self.legacy.resolve(), self.primary)
        settings = AppState(self.primary).load()
        for key in [
            "profiles",
            "active_profile",
            "chat_id",
            "feishu_secret_ref",
            "business_context",
        ]:
            self.assertEqual(settings[key], original[key])
        self.assertTrue(settings["migration_resume_requested"])
        self.assertTrue(settings["brand_bridge_pending"])
        self.assertEqual(
            settings["knowledge_files"], [str(self.primary / "knowledge/faq.md")]
        )
        self.assertTrue((self.primary / "setup/jobs/fixture/progress.json").is_file())
        self.assertEqual((self.primary / "keytool").resolve(), self.primary / "setup")
        with sqlite3.connect(self.dbfile) as db:
            self.assertEqual(
                db.execute("SELECT * FROM fixture").fetchone(),
                (42, "WXWatcher is user content"),
            )
        backup = Path(report["backup"]) / "settings.before.json"
        self.assertEqual(json.loads(backup.read_text()), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertFalse(migrate_default_state(self.home)["migrated"])

    def test_active_legacy_backend_blocks_move(self):
        with (self.legacy / "desktop.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "旧版仍在运行"):
                migrate_default_state(self.home, wait_seconds=0)
        self.assertFalse(self.primary.exists())
        self.assertFalse(self.legacy.is_symlink())

    def test_pending_client_recovery_blocks_migration(self):
        atomic_json(
            self.legacy / "keytool/jobs/fixture/progress.json",
            {"phase": "recovery_required"},
        )
        with self.assertRaisesRegex(RuntimeError, "恢复"):
            migrate_default_state(self.home)
        self.assertFalse(self.primary.exists())

    def test_existing_distinct_destination_is_never_overwritten(self):
        self.primary.mkdir()
        file = self.primary / "owned-data"
        file.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "同时存在"):
            migrate_default_state(self.home)
        self.assertEqual(file.read_text(), "preserve")
        self.assertFalse(self.legacy.is_symlink())

    def test_interrupted_post_move_write_can_resume_from_journal(self):
        real_atomic_json = atomic_json
        failed = False

        def interrupt(path, value):
            nonlocal failed
            if path == self.primary / "settings.json" and not failed:
                failed = True
                raise OSError("synthetic interruption")
            return real_atomic_json(path, value)

        with patch("wx_watcher.appstate.atomic_json", side_effect=interrupt):
            with self.assertRaises(OSError):
                migrate_default_state(self.home)
        self.assertTrue((self.primary / "branding-migration.pending.json").exists())
        self.assertTrue(migrate_default_state(self.home)["migrated"])
        self.assertFalse((self.primary / "branding-migration.pending.json").exists())
        self.assertEqual(
            AppState(self.primary).profile()["key_ref"], "unchanged-reference"
        )
        self.assertEqual(len(list((self.primary / "backups").iterdir())), 1)

    def test_dsh_migration_preserves_token_jobs_and_cost_ledger(self):
        home = self.home / "dsh"
        old = home / "storages/wx-reply-agent"
        (old / "jobs").mkdir(parents=True)
        (old / "token").write_bytes(b"synthetic-existing-token")
        (old / "token").chmod(0o600)
        (old / "jobs/completed.json").write_bytes(
            b'{"session_id":"wx-reply-legacy","status":"completed"}'
        )
        meter = home / "storages/dsh-cost-meter/ledger"
        meter.parent.mkdir()
        meter.write_bytes(b"existing ledger")
        self.assertEqual(dsh_storage(home), old)
        self.assertTrue(migrate_dsh_storage(home))
        new = home / "storages/notebridge"
        self.assertEqual(dsh_storage(home), new)
        self.assertEqual((new / "token").read_bytes(), b"synthetic-existing-token")
        self.assertEqual((new / "token").stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            (new / "jobs/completed.json").read_bytes(),
            (old / "jobs/completed.json").read_bytes(),
        )
        self.assertEqual(meter.read_bytes(), b"existing ledger")
        self.assertFalse(migrate_dsh_storage(home))

    def test_legacy_home_override_and_new_source_entry_remain_compatible(self):
        with patch.dict(os.environ, {"WX_WATCHER_HOME": str(self.legacy)}, clear=True):
            self.assertEqual(app_home(), self.legacy)
        with patch.dict(
            os.environ,
            {
                "WX_WATCHER_HOME": str(self.legacy),
                "NOTE_BRIDGE_HOME": str(self.primary),
            },
            clear=True,
        ):
            self.assertEqual(app_home(), self.primary)
        self.assertIn("note_bridge", command("desktop"))

    def test_custom_or_broken_legacy_link_does_not_create_a_blank_replacement(self):
        other = self.home / "unavailable-volume"
        custom_home = self.home / "another-user"
        legacy = custom_home / "Library/Application Support/WXWatcher"
        legacy.parent.mkdir(parents=True)
        legacy.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "自定义链接"):
            migrate_default_state(custom_home)
        self.assertFalse((legacy.parent / "NoteBridge").exists())

    def test_bridge_upgrade_only_replaces_its_own_yaml_block(self):
        home = self.home / "dsh"
        profile = home / "profiles/desktop"
        profile.mkdir(parents=True)
        prefix = "- insert:\n    - id: model-channel\n      name: synthetic-provider\n      config:\n        secret: keep-this-exactly\n"
        suffix = "- insert:\n    - id: dsh-cost-meter\n      name: dsh-cost-meter\n      config:\n        custom: unchanged\n"
        legacy_block = "- insert:\n    - id: wx-reply-agent\n      name: file:///example/old/index.mjs\n      config:\n        port: 18766\n"
        file = profile / "cordis.patch.yml"
        file.write_text(prefix + legacy_block + suffix)
        storage = home / "storages/wx-reply-agent"
        storage.mkdir(parents=True)
        (storage / "token").write_bytes(b"existing-token")
        controller = Controller(self.home / "controller")
        controller.services.shutdown_event.set()
        digest = hashlib.sha256(
            (Path(__file__).parents[1] / "integrations/dsh/index.mjs").read_bytes()
        ).hexdigest()
        try:
            with (
                patch.dict(os.environ, {"DSH_HOME": str(home)}),
                patch.object(controller, "dsh") as api,
                patch(
                    "wx_watcher.controller.socket.create_connection",
                    side_effect=OSError,
                ),
            ):
                api.return_value.request.side_effect = [
                    {
                        "ready": True,
                        "active": 0,
                        "build": "0.2.0",
                        "source_digest": "old",
                    },
                    {
                        "ready": True,
                        "active": 0,
                        "build": "0.3.0",
                        "source_digest": digest,
                    },
                ]
                self.assertTrue(controller._install_dsh()["ready"])
            result = file.read_text()
            self.assertTrue(result.startswith(prefix))
            self.assertTrue(result.endswith(suffix))
            self.assertNotIn("- id: wx-reply-agent", result)
            self.assertEqual(result.count("- id: notebridge"), 1)
            self.assertEqual(
                (home / "storages/notebridge/token").read_bytes(), b"existing-token"
            )
        finally:
            controller.pool.shutdown(wait=True)
