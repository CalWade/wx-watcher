from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .appstate import AppState, atomic_json
from .config import Config
from .reply import LocalAPI


def command(*args):
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "wx_watcher", *args]


class Services:
    def __init__(self, state: AppState):
        self.state = state
        self.children: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()
        self.started_at = None

    def running(self):
        return any(p.poll() is None for p in self.children.values())

    def start(self):
        with self.lock:
            if self.running():
                return self.status()
            reader, reply = self.state.write_configs()
            self._spawn("reader", command("--config", str(reader), "serve"))
            config = Config.load(reader)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.children["reader"].poll() is not None:
                    raise RuntimeError("微信读取服务启动失败；请检查数据库权限、密钥与端口占用")
                try:
                    if LocalAPI(config.port, config.state_dir / "api-token.txt").request("/v1/status").get("state") == "ready":
                        break
                except (OSError, RuntimeError):
                    pass
                time.sleep(.2)
            else:
                self.stop()
                raise RuntimeError("读取服务尚未就绪；请查看诊断")
            self._spawn("worker", command("reply", "--config", str(reply), "run"))
            time.sleep(.2)
            if self.children["worker"].poll() is not None:
                raise RuntimeError("回复服务启动失败，请检查 DSH 连接与配置")
            self.started_at = time.time()
            atomic_json(self.state.home / "services.json", {"pids": {k: p.pid for k, p in self.children.items()},
                        "started_at": self.started_at})
            return self.status()

    def _spawn(self, name, args):
        log = self.state.home / (name + ".log")
        if log.exists() and log.stat().st_size > 2_000_000:
            log.replace(log.with_suffix(".previous.log"))
        with log.open("ab") as stream:
            env = dict(os.environ)
            if not getattr(sys, "frozen", False):
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
            process = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True, cwd=Path.home(), env=env)
        log.chmod(0o600)
        self.children[name] = process

    def stop(self):
        with self.lock:
            for name in ("worker", "reader"):
                p = self.children.get(name)
                if p and p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(timeout=3)
            self.children.clear()
            self.started_at = None
            atomic_json(self.state.home / "services.json", {"pids": {}, "started_at": None})
            return {"running": False}

    def status(self):
        processes = {k: {"pid": p.pid, "alive": p.poll() is None} for k, p in self.children.items()}
        result = {"running": bool(processes) and all(p["alive"] for p in processes.values()),
                  "processes": processes, "started_at": self.started_at}
        try:
            reader = Config.load(self.state.home / "config/reader.toml")
            result["reader"] = LocalAPI(reader.port, reader.state_dir / "api-token.txt").request("/v1/status")
        except (OSError, RuntimeError, ValueError):
            result["reader"] = {"state": "stopped"}
        return result
