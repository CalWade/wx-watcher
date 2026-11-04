import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wx_watcher.discovery import active_roots


class AccountDiscoveryTests(unittest.TestCase):
    def test_old_account_cache_handles_do_not_make_it_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "xwechat_files"
            old = base / "previous/db_storage"
            current = base / "current/db_storage"
            old.mkdir(parents=True)
            current.mkdir(parents=True)
            paths = [
                old / "MMKV/storage_common",
                old / "message/message_0.kvdb-shm",
                old / "session/session_draft.mmkv",
                current / "contact/contact.db",
                current / "session/session.db-wal",
                current / "message/message_0.db",
            ]
            replies = [
                SimpleNamespace(stdout="123\n"),
                SimpleNamespace(stdout=str(os.getuid())),
                SimpleNamespace(stdout="\n".join("n" + str(p) for p in paths)),
            ]
            with patch("wx_watcher.discovery.subprocess.run", side_effect=replies):
                self.assertEqual(active_roots(), [current.resolve()])
