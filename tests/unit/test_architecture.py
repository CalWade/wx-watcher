"""Enforce dependency direction and provider isolation as executable rules."""

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ArchitectureTests(unittest.TestCase):
    def test_domain_has_no_application_or_infrastructure_dependencies(self):
        self.check_imports("domain", allowed_prefixes=("wx_watcher.domain",))

    def test_application_only_depends_on_domain_and_application_ports(self):
        self.check_imports(
            "application",
            allowed_prefixes=("wx_watcher.domain", "wx_watcher.application"),
        )

    def check_imports(self, layer, allowed_prefixes):
        forbidden = {
            "os",
            "subprocess",
            "sqlite3",
            "socket",
            "urllib",
            "ctypes",
            "requests",
        }
        for file in (ROOT / "wx_watcher" / layer).glob("*.py"):
            tree = ast.parse(file.read_text())
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = "." * node.level + (node.module or "")
                    modules = [
                        importlib.util.resolve_name(module, "wx_watcher." + layer)
                        if node.level
                        else module
                    ]
                for module in modules:
                    with self.subTest(file=file.name, module=module):
                        self.assertNotIn(module.split(".")[0], forbidden)
                        if module.startswith("wx_watcher."):
                            self.assertTrue(module.startswith(allowed_prefixes), module)
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, {"open", "exec", "eval"})
                    if isinstance(node.func, ast.Attribute):
                        self.assertNotIn(
                            node.func.attr,
                            {
                                "read_text",
                                "write_text",
                                "read_bytes",
                                "write_bytes",
                                "execute",
                                "executescript",
                                "Popen",
                            },
                        )

    def test_agent_business_code_has_no_provider_names_or_wire_fields(self):
        files = [ROOT / "wx_watcher/services.py"]
        for layer in ("domain", "application"):
            files.extend((ROOT / "wx_watcher" / layer).glob("*.py"))
        for file in files:
            text = file.read_text().lower()
            with self.subTest(file=str(file.relative_to(ROOT))):
                for value in ("dsh", "cost_meter", "session_id", "/v1/jobs"):
                    self.assertNotIn(value, text)
