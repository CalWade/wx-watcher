#!/usr/bin/env python3
"""真实启动 CLI 与 HTTP 服务，保存脱敏就绪状态，结束后关闭本次服务。"""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from wx_watcher.config import Config
config = Config.load(root / "config/local.toml")
process = subprocess.Popen([sys.executable, "-m", "wx_watcher", "--config",
                            str(root / "config/local.toml"), "serve"],
                           cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    deadline = time.monotonic() + 8
    result = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务启动失败：" + process.stderr.read())
        try:
            token = (config.state_dir / "api-token.txt").read_text().strip()
            url = f"http://127.0.0.1:{config.port}/v1/status"
            with urlopen(Request(url, headers={"Authorization": "Bearer " + token}), timeout=1) as response:
                result = json.load(response)
            if result["state"] != "starting":
                break
        except (OSError, URLError):
            pass
        time.sleep(0.1)
    if result is None or result["state"] == "starting":
        raise RuntimeError("服务未在时限内返回扫描状态")
    try:
        urlopen(f"http://127.0.0.1:{config.port}/v1/messages", timeout=1)
    except HTTPError as exc:
        assert exc.code == 401
    else:
        raise AssertionError("未认证请求没有被拒绝")
    result["http_api_verified"] = True
    result["unauthenticated_request_rejected"] = True
    (root / "output/live-preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
finally:
    process.terminate()
    try:
        process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
