#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
suite = unittest.defaultTestLoader.discover(str(root / "tests"))
started = time.monotonic()
result = unittest.TextTestRunner(verbosity=2).run(suite)
report = {
    "scope": "Synthetic SQLite and real SQLCipher4 fixtures; no personal WeChat data",
    "tests": result.testsRun, "failures": len(result.failures),
    "errors": len(result.errors), "skipped": len(result.skipped),
    "elapsed_seconds": round(time.monotonic() - started, 3),
    "sqlcipher": subprocess.check_output(["/opt/homebrew/bin/sqlcipher", "-version"], text=True).strip(),
    "live_wechat_verified": False,
}
(root / "output").mkdir(exist_ok=True)
(root / "output/verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
raise SystemExit(0 if result.wasSuccessful() else 1)
