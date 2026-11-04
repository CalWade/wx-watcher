"""Determine which local account the official WeChat process actually has open."""

import os
import subprocess
from pathlib import Path


def active_roots() -> list[Path]:
    try:
        pids = subprocess.run(
            ["/usr/bin/pgrep", "-x", "WeChat"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.split()
        roots = set()
        for pid in pids:
            owner = subprocess.run(
                ["/bin/ps", "-p", pid, "-o", "uid="],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            if owner != str(os.getuid()):
                continue
            result = subprocess.run(
                ["/usr/sbin/lsof", "-a", "-p", pid, "-Fn"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                if (
                    line.startswith("n")
                    and "/xwechat_files/" in line
                    and "/db_storage/" in line
                ):
                    root = Path(line[1:].split("/db_storage/", 1)[0]) / "db_storage"
                    if root.is_dir():
                        roots.add(root.resolve())
        return sorted(roots)
    except (OSError, subprocess.TimeoutExpired):
        return []
