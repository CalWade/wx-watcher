import tempfile
import unittest
from pathlib import Path

from wx_watcher.knowledge import select_passages


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
