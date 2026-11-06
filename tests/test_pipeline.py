"""使用真实 SQLite/SQLCipher WAL 文件；全部为合成数据，不接触本人微信。"""

from __future__ import annotations

import ctypes as c
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import zstandard

from wx_watcher.api import create_server
from wx_watcher.collector import Collector
from wx_watcher.config import Config, default_sqlcipher
from wx_watcher.infrastructure.sqlcipher import library_available, library_path
from wx_watcher.store import Store

CHAT = "123456@chatroom"
GROUP = "客户服务测试群"
TABLE = "Msg_" + hashlib.md5(CHAT.encode()).hexdigest()
MASTER = bytes(range(32))

LIBRARY = library_path()


class NativeDB:
    def __init__(self, path: Path):
        self.lib = c.CDLL(str(LIBRARY))
        self.lib.sqlite3_open.argtypes = [c.c_char_p, c.POINTER(c.c_void_p)]
        self.lib.sqlite3_key.argtypes = [c.c_void_p, c.c_void_p, c.c_int]
        self.lib.sqlite3_exec.argtypes = [
            c.c_void_p,
            c.c_char_p,
            c.c_void_p,
            c.c_void_p,
            c.c_void_p,
        ]
        self.lib.sqlite3_errmsg.argtypes = [c.c_void_p]
        self.lib.sqlite3_errmsg.restype = c.c_char_p
        self.lib.sqlite3_close.argtypes = [c.c_void_p]
        self.db = c.c_void_p()
        if self.lib.sqlite3_open(str(path).encode(), c.byref(self.db)):
            raise RuntimeError("Cannot open fixture")
        key = c.create_string_buffer(MASTER)
        if self.lib.sqlite3_key(self.db, key, len(MASTER)):
            raise RuntimeError("Cannot key fixture")
        self.exec(
            "PRAGMA cipher_compatibility=4; PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;"
        )

    def exec(self, sql):
        if self.lib.sqlite3_exec(self.db, sql.encode(), None, None, None):
            raise RuntimeError(self.lib.sqlite3_errmsg(self.db).decode())

    def close(self):
        self.lib.sqlite3_close(self.db)


class PlainDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(
            "PRAGMA page_size=4096; PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;"
        )

    def exec(self, sql):
        self.conn.executescript(sql)

    def close(self):
        self.conn.close()


class Fixture:
    def __init__(self, base: Path, encrypted=False):
        self.root = base / "account/db_storage"
        (self.root / "message").mkdir(parents=True)
        (self.root / "contact").mkdir()
        self.kind = NativeDB if encrypted else PlainDB
        self.handles = []
        key = base / "key.txt"
        key.write_text(MASTER.hex())
        key.chmod(0o600)
        self.config = Config(
            db_root=self.root,
            key_file=key,
            state_dir=base / "state",
            sqlcipher=default_sqlcipher(),
            group_name=GROUP,
            backend="sqlcipher" if encrypted else "sqlite",
        )
        self.contact = self.new(self.root / "contact/contact.db")
        self.contact.exec(
            "CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT);"
            f"INSERT INTO contact VALUES ('{CHAT}','{GROUP}','');"
        )
        self.shard = self.add_shard(0, "wxid_alice")
        self.insert(self.shard, 1, "已有消息")

    def new(self, path):
        handle = self.kind(path)
        self.handles.append(handle)
        return handle

    def add_shard(self, index, sender):
        db = self.new(self.root / f"message/message_{index}.db")
        db.exec(
            "CREATE TABLE Name2Id(user_name TEXT);"
            f"INSERT INTO Name2Id VALUES ('{CHAT}'),('{sender}');"
            f'CREATE TABLE "{TABLE}"(local_id INTEGER PRIMARY KEY, server_id INTEGER, '
            "create_time INTEGER, local_type INTEGER, real_sender_id INTEGER, "
            "message_content BLOB, sort_seq INTEGER);"
        )
        return db

    def insert(self, db, local_id, text):
        value = text.replace("'", "''")
        # 故意让所有消息 sort_seq 相同，防止单字段水位漏消息。
        db.exec(
            f'INSERT INTO "{TABLE}" VALUES ({local_id},{100 + local_id},1700000000,1,2,'
            f"'{value}',1000);"
        )

    def close(self):
        for db in self.handles:
            db.close()


class PipelineTests(unittest.TestCase):
    encrypted = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.fixture = Fixture(self.base, self.encrypted)
        self.store = Store(self.fixture.config.state_dir)
        self.collector = Collector(self.fixture.config, self.store)

    def tearDown(self):
        self.collector.stop()
        self.store.close()
        self.fixture.close()
        self.temp.cleanup()

    def test_empty_target_reads_a_real_direct_chat_and_can_pin_it(self):
        name = "filehelper"
        table = "Msg_" + hashlib.md5(name.encode()).hexdigest()
        self.fixture.contact.exec(
            "INSERT INTO contact VALUES ('filehelper','文件传输助手','');"
        )
        self.fixture.shard.exec(
            f'CREATE TABLE "{table}" AS SELECT * FROM "{TABLE}" WHERE 0;'
            f"INSERT INTO \"{table}\" VALUES (1,991,1700000001,1,2,'助手消息',1000);"
        )
        config = replace(self.fixture.config, group_name="", chat_id="")
        collector = Collector(config, self.store)
        collector.poll()
        self.assertEqual(collector.status()["chat_id"], name)
        self.assertEqual(collector.status()["group_name"], "文件传输助手")
        self.assertEqual([m["content"] for m in self.store.history(10)], ["助手消息"])
        self.fixture.insert(self.fixture.shard, 2, "其他会话消息")
        pinned = Collector(replace(config, chat_id=name), self.store)
        pinned.poll()
        self.assertEqual(self.store.events(0, 20)["events"], [])
        self.assertEqual([m["content"] for m in self.store.history(10)], ["助手消息"])

    def test_builtin_helper_first_message_becomes_a_new_event(self):
        config = replace(
            self.fixture.config, group_name="文件传输助手", chat_id="filehelper"
        )
        collector = Collector(config, self.store)
        collector.poll()
        self.assertEqual(self.store.history(10), [])
        self.assertFalse(collector.status()["live_verified"])
        table = "Msg_" + hashlib.md5(b"filehelper").hexdigest()
        self.fixture.shard.exec(
            f'CREATE TABLE "{table}" AS SELECT * FROM "{TABLE}" WHERE 0;'
            f"INSERT INTO \"{table}\" VALUES (1,992,1700000002,1,2,'助手首条测试',1000);"
        )
        collector.poll()
        events = self.store.events(0, 10)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["content"], "助手首条测试")
        self.assertTrue(collector.status()["live_verified"])
        restarted = Collector(config, self.store)
        restarted.poll()
        self.assertTrue(restarted.status()["live_verified"])

    def test_committed_wal_visible_and_source_unchanged(self):
        self.collector.poll()
        self.assertEqual(self.store.events(0, 100)["events"], [])
        self.fixture.insert(self.fixture.shard, 2, "WAL 中的新消息\n第二行")
        paths = [p for p in self.fixture.root.rglob("*") if p.is_file()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        self.assertGreater(
            (self.fixture.root / "message/message_0.db-wal").stat().st_size, 32
        )
        self.assertEqual(self.collector.poll(), 1)
        event = self.store.events(0, 100)["events"][0]
        self.assertEqual(event["content"], "WAL 中的新消息\n第二行")
        self.assertEqual(event["sender"], "wxid_alice")
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        self.assertEqual(before, after)
        self.assertEqual(self.collector.poll(), 0)

    def test_restart_resumes_and_client_cursor_replays(self):
        self.collector.poll()
        self.fixture.insert(self.fixture.shard, 2, "第一条增量")
        self.collector.poll()
        first = self.store.events(0, 100)
        self.store.close()
        self.store = Store(self.fixture.config.state_dir)
        self.collector = Collector(self.fixture.config, self.store)
        self.fixture.insert(self.fixture.shard, 3, "重启后")
        self.collector.poll()
        self.assertEqual(len(self.store.events(0, 100)["events"]), 2)
        following = self.store.events(first["next_cursor"], 100)
        self.assertEqual([e["content"] for e in following["events"]], ["重启后"])
        self.assertEqual(self.store.events(first["next_cursor"], 100), following)

    def test_new_shard_first_message_and_sender_namespace(self):
        self.collector.poll()
        second = self.fixture.add_shard(1, "wxid_bob")
        self.fixture.insert(second, 1, "新分片首条")
        self.fixture.insert(self.fixture.shard, 2, "原分片消息")
        self.assertEqual(self.collector.poll(), 2)
        events = self.store.events(0, 100)["events"]
        self.assertEqual(
            {e["content"]: e["sender"] for e in events},
            {"新分片首条": "wxid_bob", "原分片消息": "wxid_alice"},
        )
        self.assertEqual(len({e["message_id"] for e in events}), 2)

    def test_group_ambiguity_never_initializes_watermarks(self):
        self.fixture.contact.exec(
            f"INSERT INTO contact VALUES ('999@chatroom','{GROUP}','');"
        )
        with self.assertRaisesRegex(ValueError, "精确匹配 2 条"):
            self.collector.poll()
        self.assertFalse(self.store.initialized())
        self.assertEqual(self.store.watermarks(), {})

    def test_failed_scan_keeps_watermark_then_recovers(self):
        self.collector.poll()
        before = self.store.watermarks()
        self.fixture.insert(self.fixture.shard, 2, "错误恢复后仍须收到")
        with patch.object(
            self.collector.source, "read_shard", side_effect=RuntimeError("temporary")
        ):
            with self.assertRaises(RuntimeError):
                self.collector.poll()
        self.assertEqual(self.store.watermarks(), before)
        self.assertEqual(self.collector.poll(), 1)

    def test_underlying_query_readonly(self):
        reader = self.collector.source.reader
        with self.assertRaises((sqlite3.OperationalError, RuntimeError)):
            reader.rows(
                self.fixture.root / "contact/contact.db", "DELETE FROM contact;"
            )
        self.assertEqual(
            len(
                reader.rows(
                    self.fixture.root / "contact/contact.db",
                    "SELECT username FROM contact;",
                )
            ),
            1,
        )

    def test_scope_cannot_mix_accounts_or_groups(self):
        self.collector.poll()
        with self.assertRaisesRegex(ValueError, "账号或目标群"):
            self.store.bind_scope("some other account")

    def test_state_directory_cannot_write_into_source_tree(self):
        bad = self.fixture.root / "state"
        with self.assertRaisesRegex(ValueError, "相互独立"):
            replace(self.fixture.config, state_dir=bad)
        self.assertFalse(bad.exists())

    def test_doctor_excludes_search_and_resource_databases(self):
        from wx_watcher.__main__ import doctor

        for name in ("message_fts.db", "message_resource.db"):
            (self.fixture.root / "message" / name).write_bytes(b"not a message shard")
        self.assertEqual(doctor(self.fixture.config)["message_shards"], 1)

    def test_same_second_history_uses_numeric_local_id(self):
        self.collector.poll()
        for lid in range(2, 13):
            self.fixture.insert(self.fixture.shard, lid, f"同秒第 {lid} 条")
        self.collector.poll()
        self.assertEqual([m["local_id"] for m in self.store.history(3)], [10, 11, 12])

    def test_unrelated_group_is_not_exposed(self):
        self.collector.poll()
        self.fixture.shard.exec(
            f'CREATE TABLE "Msg_other" AS SELECT * FROM "{TABLE}";'
            "UPDATE Msg_other SET message_content='其他群的内容';"
        )
        self.assertEqual(self.collector.poll(), 0)
        self.assertTrue(
            all(m["content"] != "其他群的内容" for m in self.store.history(100))
        )

    def test_compressed_chinese_preserves_body_and_removes_sender_envelope(self):
        self.collector.poll()
        body = "请检查部署日志\n错误码：502，链接：https://example.invalid/log"
        compressed = zstandard.ZstdCompressor().compress(
            ("wxid_alice:\n" + body).encode()
        )
        self.fixture.shard.exec(
            f'INSERT INTO "{TABLE}" VALUES (2,102,1700000000,1,2,'
            f"X'{compressed.hex()}',1000);"
        )
        self.collector.poll()
        self.assertEqual(self.store.events(0, 100)["events"][0]["content"], body)

    def test_http_auth_readonly_and_event_cursor(self):
        self.collector.poll()
        server = create_server(self.store, self.collector, "test-secret-token", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)

        def request(path, method="GET", auth=True):
            client = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            headers = {"Authorization": "Bearer test-secret-token"} if auth else {}
            client.request(method, path, headers=headers)
            response = client.getresponse()
            result = response.status, json.loads(response.read())
            client.close()
            return result

        self.assertEqual(request("/v1/messages", auth=False)[0], 401)
        self.assertEqual(request("/v1/messages", "POST")[0], 405)
        self.assertEqual(request("/v1/messages?limit=-1")[0], 400)
        status, body = request("/v1/messages")
        self.assertEqual(status, 200)
        self.assertEqual(body["messages"][-1]["content"], "已有消息")
        self.fixture.insert(self.fixture.shard, 2, "API 增量")
        self.collector.poll()
        _, page = request("/v1/events?after=0")
        self.assertEqual(page["events"][0]["content"], "API 增量")
        self.assertEqual(
            request(f"/v1/events?after={page['next_cursor']}")[1]["events"], []
        )


@unittest.skipUnless(library_available(), "真实 SQLCipher 动态库未安装")
class EncryptedPipelineTests(PipelineTests):
    encrypted = True

    def test_wrong_key_fails_without_logging_key(self):
        self.fixture.config.key_file.write_text(bytes(reversed(MASTER)).hex())
        with self.assertRaises(RuntimeError) as failure:
            self.collector.poll()
        self.assertNotIn(bytes(reversed(MASTER)).hex(), str(failure.exception))
        self.assertFalse(self.store.initialized())

    def test_open_transaction_not_published_before_commit(self):
        self.collector.poll()
        self.fixture.shard.exec(
            f"BEGIN; INSERT INTO \"{TABLE}\" VALUES (2,102,1700000000,1,2,'尚未提交',1000);"
        )
        self.assertEqual(self.collector.poll(), 0)
        self.fixture.shard.exec("COMMIT;")
        self.assertEqual(self.collector.poll(), 1)
        self.assertEqual(self.store.events(0, 100)["events"][0]["content"], "尚未提交")

    def test_corrected_key_recovers_without_restart(self):
        key = self.fixture.config.key_file
        key.write_text(bytes(reversed(MASTER)).hex())
        with self.assertRaises(RuntimeError):
            self.collector.poll()
        key.write_text(MASTER.hex())
        self.collector.poll()
        self.assertEqual(self.store.history(1)[0]["content"], "已有消息")


if __name__ == "__main__":
    unittest.main()
