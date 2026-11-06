#!/usr/bin/env python3
"""Run the reproducible project checks and retain per-stage reports."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stages(profile, lldb=False):
    checks = []
    if profile in {"full", "core"}:
        checks.extend(
            [
                (
                    "python-lint",
                    [
                        "uv",
                        "run",
                        "ruff",
                        "check",
                        "wx_watcher",
                        "note_bridge",
                        "scripts",
                        "tests",
                    ],
                ),
                (
                    "python-format",
                    [
                        "uv",
                        "run",
                        "ruff",
                        "format",
                        "--check",
                        "wx_watcher",
                        "note_bridge",
                        "scripts",
                        "tests",
                    ],
                ),
                (
                    "python-pyramid",
                    ["uv", "run", "python", "scripts/verify.py", "--coverage"],
                ),
                ("agent-bridge", ["node", "--test", "tests/dsh/bridge.test.mjs"]),
            ]
        )
        if sys.platform == "darwin":
            checks.append(
                (
                    "native-lldb" if lldb else "native",
                    ["swift", "test", "--package-path", "native", "-c", "release"],
                )
            )
        checks.append(("browser-pipeline", ["npm", "run", "test:e2e"]))
    if profile in {"full", "ui"}:
        checks.extend(
            [
                ("javascript-syntax", ["node", "--check", "wx_watcher/assets/app.js"]),
                ("bridge-syntax", ["node", "--check", "integrations/dsh/index.mjs"]),
                ("browser-ui", ["npm", "test"]),
            ]
        )
    checks.extend(
        [
            ("source-distribution", ["uv", "build"]),
            ("release-privacy", ["uv", "run", "python", "scripts/check_release.py"]),
        ]
    )
    return checks


def run(profile, lldb=False):
    output = ROOT / "output/checks"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    env = dict(os.environ)
    env["NOTEBRIDGE_TEST_LLDB"] = "1" if lldb else "0"
    for name, command in stages(profile, lldb):
        print(f"Running {name}", flush=True)
        started = time.monotonic()
        log = output / (name + ".log")
        with log.open("w") as stream:
            try:
                result = subprocess.run(
                    command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT
                )
                code = result.returncode
            except OSError as error:
                stream.write(f"Unable to start {command[0]}: {type(error).__name__}\n")
                code = 127
        results.append(
            {
                "stage": name,
                "passed": code == 0,
                "exit_code": code,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "log": str(log.relative_to(ROOT)),
            }
        )
        if code:
            print(log.read_text()[-12000:], file=sys.stderr)
            break
    report = {
        "passed": len(results) == len(stages(profile, lldb))
        and all(r["passed"] for r in results),
        "profile": profile,
        "native_lldb_enabled": lldb,
        "native_available": sys.platform == "darwin",
        "real_account_acceptance": "manual",
        "stages": results,
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["full", "core", "ui"], default="full")
    parser.add_argument(
        "--lldb",
        action="store_true",
        help="Enable isolated native debugger tests on macOS",
    )
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.lldb and sys.platform != "darwin":
        parser.error("--lldb requires macOS")
    if args.list:
        print(json.dumps(stages(args.profile, args.lldb), indent=2))
        return 0
    return 0 if run(args.profile, args.lldb)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
