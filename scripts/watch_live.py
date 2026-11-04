#!/usr/bin/env python3
"""Verify a user-sent marker through the local, read-only event API."""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from wx_watcher.config import Config

parser = argparse.ArgumentParser()
parser.add_argument("--marker", default="wx-test-1103")
parser.add_argument("--timeout", type=int, default=300)
args = parser.parse_args()
config = Config.load(root / "config/local.toml")
token = (config.state_dir / "api-token.txt").read_text().strip()


def get(resource):
    request = Request(
        f"http://127.0.0.1:{config.port}/v1/{resource}",
        headers={"Authorization": "Bearer " + token},
    )
    with urlopen(request, timeout=3) as response:
        return json.load(response)


cursor = get("events?after=0&limit=1000")["next_cursor"]
started = datetime.now(UTC).isoformat()
print(
    json.dumps(
        {
            "state": "waiting_for_marker",
            "marker": args.marker,
            "started_at": started,
            "after_cursor": cursor,
        }
    ),
    flush=True,
)
deadline = time.monotonic() + args.timeout
while time.monotonic() < deadline:
    try:
        data = get(f"events?after={cursor}&limit=1000")
        for event in data["events"]:
            if event["content"].strip() == args.marker:
                received = time.time()
                delay = round(received - event["created_at"], 3)
                report = {
                    "state": "verified",
                    "scope": "Real local WeChat message through HTTP event API",
                    "started_at": started,
                    "received_at": datetime.now(UTC).isoformat(),
                    "event_id": event["event_id"],
                    "chat_id": event["chat_id"],
                    "content": args.marker,
                    "created_at": event["created_at"],
                    "message_to_api_seconds": delay,
                    "timestamp_resolution_seconds": 1,
                    "within_10_seconds": 0 <= delay <= 10,
                    "status": get("status"),
                }
                path = root / "output/live-message-verification.json"
                fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as out:
                    json.dump(report, out, ensure_ascii=False, indent=2)
                    out.write("\n")
                print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
                raise SystemExit(0 if report["within_10_seconds"] else 2)
        cursor = data["next_cursor"]
    except (HTTPError, URLError, TimeoutError):
        pass
    time.sleep(0.2)
print(json.dumps({"state": "timeout", "marker": args.marker}), flush=True)
raise SystemExit(3)
