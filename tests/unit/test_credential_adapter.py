import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wx_watcher.credentials import load_reference, native_request, native_tool
from wx_watcher.keyflow import KeyFlow


class CredentialAdapterTests(unittest.TestCase):
    def test_capture_jobs_keep_the_native_helpers_canonical_location_with_custom_data(
        self,
    ):
        with patch(
            "wx_watcher.keyflow.Path.home", return_value=Path("/synthetic/user")
        ):
            flow = KeyFlow(SimpleNamespace(home=Path("/synthetic/custom-data")))
        self.assertEqual(
            flow.jobs,
            Path("/synthetic/user/Library/Application Support/NoteBridge/setup/jobs"),
        )

    def test_explicit_native_tool_is_resolved_without_process_execution(self):
        with (
            patch("wx_watcher.credentials.Path.is_file", return_value=True),
            patch("wx_watcher.credentials.os.access", return_value=True),
            patch("wx_watcher.credentials.subprocess.run") as run,
        ):
            self.assertEqual(
                native_tool("/synthetic/access"), Path("/synthetic/access")
            )
        run.assert_not_called()
        with (
            patch("wx_watcher.credentials.Path.is_file", return_value=False),
            self.assertRaises(FileNotFoundError),
        ):
            native_tool("/synthetic/missing")

    def test_secret_payloads_only_cross_the_private_pipe_and_errors_are_bounded(self):
        request = {"operation": "store_secret", "secret": "synthetic-private-value"}
        with (
            patch(
                "wx_watcher.credentials.native_tool",
                return_value=Path("/synthetic/access"),
            ),
            patch(
                "wx_watcher.credentials.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 0, '{"ok":true,"key_ref":"synthetic-reference"}', ""
                ),
            ) as run,
        ):
            self.assertTrue(native_request(request, internal_read=True)["ok"])
        args, kwargs = run.call_args
        self.assertNotIn(request["secret"], str(args))
        self.assertEqual(json.loads(kwargs["input"]), request)
        self.assertIn("--internal-read", args[0])
        for stdout, code, exception in [
            ("not-json", 1, RuntimeError),
            ('{"ok":false,"error":"database_access_denied"}', 1, PermissionError),
            ('{"ok":false,"error":"refused"}', 1, RuntimeError),
        ]:
            with (
                self.subTest(stdout=stdout),
                patch(
                    "wx_watcher.credentials.native_tool",
                    return_value=Path("/synthetic/access"),
                ),
                patch(
                    "wx_watcher.credentials.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        [], code, stdout, request["secret"]
                    ),
                ),
                self.assertRaises(exception) as result,
            ):
                native_request(request)
            self.assertNotIn(request["secret"], str(result.exception))

    def test_reference_reads_validate_key_length_and_hex(self):
        with patch(
            "wx_watcher.credentials.native_request",
            return_value={"key_hex": bytes(range(32)).hex()},
        ):
            self.assertEqual(load_reference("synthetic-reference"), bytes(range(32)))
        for response in [{}, {"key_hex": "not-hex"}, {"key_hex": "01"}]:
            with (
                self.subTest(response=response),
                patch("wx_watcher.credentials.native_request", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                load_reference("synthetic-reference")
