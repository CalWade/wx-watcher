import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check, dev


class EnvironmentToolTests(unittest.TestCase):
    def test_package_check_rejects_an_unavailable_backend_before_starting_processes(
        self,
    ):
        with (
            patch("scripts.dev.Path.is_file", return_value=False),
            patch("scripts.dev.subprocess.Popen") as start,
            self.assertRaisesRegex(ValueError, "backend/notebridge"),
        ):
            dev.package_acceptance("/synthetic/missing-backend")
        start.assert_not_called()

    def test_check_plan_has_each_test_layer_and_a_failure_stops_later_stages(self):
        names = [name for name, command in check.stages("full")]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(
            {"python-pyramid", "browser-ui", "browser-pipeline"} <= set(names)
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(check, "ROOT", Path(tmp)),
            patch.object(check.subprocess, "run", side_effect=FileNotFoundError) as run,
        ):
            result = check.run("ui")
            report = json.loads((Path(tmp) / "output/checks/summary.json").read_text())
        self.assertFalse(result["passed"])
        self.assertFalse(report["passed"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report["stages"][0]["exit_code"], 127)

    def test_doctor_reports_missing_tools_before_running_any_installation(self):
        with (
            patch("scripts.dev.shutil.which", return_value=None),
            patch(
                "wx_watcher.infrastructure.sqlcipher.library_available",
                return_value=False,
            ),
            patch("scripts.dev.subprocess.run") as run,
        ):
            result = dev.doctor(native=True)
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["sqlcipher"])
        run.assert_not_called()

    def test_bootstrap_uses_project_lock_files_and_installs_browser_runtime(self):
        with (
            patch("scripts.dev.shutil.which", return_value="/synthetic/tool"),
            patch("scripts.dev.subprocess.run") as run,
        ):
            self.assertTrue(dev.bootstrap()["completed"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["uv", "sync", "--frozen", "--group", "build"],
                ["npm", "ci", "--ignore-scripts"],
                ["npx", "--no-install", "playwright", "install", "chromium"],
            ],
        )

    def test_missing_package_manager_produces_an_actionable_bootstrap_result(self):
        with (
            patch(
                "scripts.dev.shutil.which",
                side_effect=lambda name: (
                    "/synthetic/tool" if name in {"node", "npm"} else None
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "SQLCipher"),
        ):
            dev.bootstrap()

    def test_missing_node_is_reported_before_installing_other_dependencies(self):
        with (
            patch("scripts.dev.shutil.which", return_value=None),
            patch("scripts.dev.subprocess.run") as run,
            self.assertRaisesRegex(RuntimeError, "Node.js"),
        ):
            dev.bootstrap()
        run.assert_not_called()
