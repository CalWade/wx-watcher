#!/usr/bin/env python3
"""同机 Agent/外部程序调用示例；不把凭据写进命令参数或 URL。"""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wx_watcher.config import Config

parser = argparse.ArgumentParser()
parser.add_argument("resource", choices=["status", "messages", "events"])
parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config/local.toml")
parser.add_argument("--limit", type=int, default=50)
parser.add_argument("--after", type=int, default=0)
args = parser.parse_args()
config = Config.load(args.config)
token = (config.state_dir / "api-token.txt").read_text().strip()
url = f"http://127.0.0.1:{config.port}/v1/{args.resource}"
if args.resource != "status":
    url += f"?limit={args.limit}&after={args.after}"
request = Request(url, headers={"Authorization": "Bearer " + token})
try:
    with urlopen(request, timeout=10) as response:
        print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
except HTTPError as exc:
    print(exc.read().decode(), file=sys.stderr)
    raise SystemExit(1)
