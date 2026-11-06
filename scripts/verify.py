#!/usr/bin/env python3
"""Run the unit/integration pyramid and enforce measured coverage thresholds."""

import argparse
import json
import os
import sys
import time
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test


def load_layer(layer):
    if layer == "unit":
        return unittest.defaultTestLoader.discover(
            str(ROOT / "tests/unit"), top_level_dir=str(ROOT / "tests")
        )
    discovered = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), top_level_dir=str(ROOT / "tests")
    )
    return unittest.TestSuite(
        test for test in cases(discovered) if not test.id().startswith("unit.")
    )


def coverage_metrics(data, prefix=""):
    summaries = [
        value["summary"]
        for file, value in data["files"].items()
        if file.startswith(prefix)
    ]
    lines = sum(row["num_statements"] for row in summaries)
    covered = sum(row["covered_lines"] for row in summaries)
    branches = sum(row["num_branches"] for row in summaries)
    covered_branches = sum(row["covered_branches"] for row in summaries)
    return {
        "lines": round(100 * covered / lines, 2) if lines else 100,
        "branches": round(100 * covered_branches / branches, 2) if branches else 100,
        "statements": lines,
        "branch_count": branches,
    }


def enforce_coverage(data, layer):
    policy = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["notebridge"][
        "quality"
    ]
    metrics = {
        name: coverage_metrics(data, prefix)
        for name, prefix in [
            ("domain", "wx_watcher/domain/"),
            ("application", "wx_watcher/application/"),
            ("overall", ""),
        ]
    }
    errors = []
    for name, values in metrics.items():
        if layer == "integration":
            continue
        if name == "overall" and layer != "all":
            continue
        for metric in ["lines", "branches"]:
            threshold = policy[name + "_" + metric]
            if values[metric] < threshold:
                errors.append(f"{name} {metric}: {values[metric]} < {threshold}")
    return metrics, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layer", choices=["unit", "integration", "all"], default="all"
    )
    parser.add_argument("--coverage", action="store_true")
    args = parser.parse_args()
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    started = time.monotonic()
    coverage = None
    previous_context = os.environ.get("NOTE_BRIDGE_COVERAGE_CONTEXT")
    if args.coverage:
        from coverage import Coverage

        coverage = Coverage(config_file=str(ROOT / "pyproject.toml"))
        coverage.erase()
        coverage.start()

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            if coverage:
                os.environ["NOTE_BRIDGE_COVERAGE_CONTEXT"] = test.id()
                coverage.switch_context(test.id())
            super().startTest(test)

    layers = ["unit", "integration"] if args.layer == "all" else [args.layer]
    results = {}
    try:
        if "integration" in layers:
            import shutil

            from wx_watcher.config import default_sqlcipher
            from wx_watcher.infrastructure.sqlcipher import library_available

            if not shutil.which(default_sqlcipher()) or not library_available():
                raise RuntimeError(
                    "集成测试需要真实 SQLCipher 和动态库，请运行 scripts/dev.py bootstrap"
                )
        for layer in layers:
            print(f"Running {layer} tests", flush=True)
            result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(
                load_layer(layer)
            )
            results[layer] = {
                "tests": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": len(result.skipped),
                "passed": result.wasSuccessful() and not result.skipped,
            }
    except RuntimeError as error:
        results["environment"] = {
            "tests": 0,
            "failures": 0,
            "errors": 1,
            "skipped": 0,
            "passed": False,
            "message": str(error),
        }
    finally:
        if coverage:
            coverage.stop()
            coverage.save()
            coverage.combine(keep=True)
            coverage.save()
            coverage.json_report(
                outfile=str(output / "coverage/coverage.json"), show_contexts=True
            )
            coverage.xml_report(outfile=str(output / "coverage/coverage.xml"))
            coverage.html_report(directory=str(output / "coverage/html"))
        if previous_context is None:
            os.environ.pop("NOTE_BRIDGE_COVERAGE_CONTEXT", None)
        else:
            os.environ["NOTE_BRIDGE_COVERAGE_CONTEXT"] = previous_context
    report = {
        "scope": "Unit ports and real local integration peers with synthetic encrypted data",
        "layers": results,
        "tests": sum(value["tests"] for value in results.values()),
        "failures": sum(value["failures"] for value in results.values()),
        "errors": sum(value["errors"] for value in results.values()),
        "skipped": sum(value["skipped"] for value in results.values()),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "live_wechat_verified": False,
    }
    errors = []
    if coverage:
        data = json.loads((output / "coverage/coverage.json").read_text())
        report["coverage"], errors = enforce_coverage(data, args.layer)
        report["coverage_errors"] = errors
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (
        0 if all(result["passed"] for result in results.values()) and not errors else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
