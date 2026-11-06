"""Every supported feature declares runnable tests and every action has an owner."""

import importlib
import json
import re
import unittest
from pathlib import Path

from wx_watcher.application.accounts import AccountsUseCases
from wx_watcher.application.connections import ConnectionsUseCases
from wx_watcher.application.credentials import CredentialsUseCases
from wx_watcher.application.runtime import RuntimeUseCases
from wx_watcher.application.settings import SettingsUseCases
from wx_watcher.application.tasks import TasksUseCases

ROOT = Path(__file__).resolve().parents[2]


class FeatureContractTests(unittest.TestCase):
    def setUp(self):
        self.features = json.loads((ROOT / "tests/features.json").read_text())[
            "features"
        ]

    def test_all_application_commands_have_one_feature_owner_and_unit_evidence(self):
        expected = {
            operation
            for group in [
                AccountsUseCases,
                CredentialsUseCases,
                SettingsUseCases,
                ConnectionsUseCases,
                RuntimeUseCases,
                TasksUseCases,
            ]
            for operation in group.operations
        }
        owned = [
            operation for feature in self.features for operation in feature["commands"]
        ]
        self.assertEqual(len(owned), len(set(owned)))
        self.assertEqual(set(owned), expected)
        self.assertEqual(len(self.features), len({f["id"] for f in self.features}))
        for feature in self.features:
            with self.subTest(feature=feature["id"]):
                self.assertTrue(feature["unit"] or feature["native_unit"])
                for name in feature["unit"]:
                    module_name, class_name, method = name.rsplit(".", 2)
                    cls = getattr(importlib.import_module(module_name), class_name)
                    self.assertTrue(issubclass(cls, unittest.TestCase))
                    self.assertTrue(callable(getattr(cls, method)))

    def test_integration_browser_and_native_evidence_resolves_to_repository_tests(self):
        for feature in self.features:
            with self.subTest(feature=feature["id"]):
                self.assertTrue(feature["integration"] or feature["native_unit"])
                for name in feature["integration"] + feature["browser"]:
                    self.assertTrue((ROOT / name).is_file(), name)
                for name in feature["native_unit"]:
                    file, method = name.split("::")
                    source = (ROOT / file).read_text()
                    self.assertRegex(
                        source, r"@Test[^\n]*\bfunc\s+" + re.escape(method) + r"\("
                    )
