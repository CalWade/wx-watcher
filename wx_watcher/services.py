from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from .appstate import AppState, atomic_json
from .config import Config
from .reply import LocalAPI


def command(*args):
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "note_bridge", *args]


class Services:
    def __init__(self, state: AppState):
        self.state = state
        self.children: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()
        self.started_at = None
        self.expected_running = False
        self.restarts = {"reader": [], "worker": []}
        self.last_error = None
        self.shutdown_event = threading.Event()
        threading.Thread(
            target=self._watch, daemon=True, name="notebridge-supervisor"
        ).start()

    def running(self):
        return any(p.poll() is None for p in self.children.values())

    def start(self):
        with self.lock:
            if self.running():
                return self.status()
            self.last_error = None
            self.restarts = {"reader": [], "worker": []}
            try:
                return self._start()
            except (OSError, RuntimeError, ValueError) as exc:
                self.stop()
                self.last_error = str(exc)
                raise

    def _start(self):
        requested = self.state.load()["reader_port"]
        with socket.socket() as reservation:
            try:
                reservation.bind(("127.0.0.1", requested))
            except OSError:
                reservation.bind(("127.0.0.1", 0))
            selected = reservation.getsockname()[1]
        if requested != selected:
            self.state.update(reader_port=selected)
        reader, reply = self.state.write_configs()
        self._spawn("reader", command("--config", str(reader), "serve"))
        config = Config.load(reader)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.children["reader"].poll() is not None:
                raise RuntimeError(
                    "微信读取服务启动失败；请检查数据库权限、密钥与端口占用"
                )
            try:
                if (
                    LocalAPI(config.port, config.state_dir / "api-token.txt")
                    .request("/v1/status")
                    .get("state")
                    == "ready"
                ):
                    break
            except (OSError, RuntimeError):
                pass
            time.sleep(0.2)
        else:
            self.stop()
            raise RuntimeError("读取服务尚未就绪；请查看诊断")
        self._spawn("worker", command("reply", "--config", str(reply), "run"))
        time.sleep(0.2)
        if self.children["worker"].poll() is not None:
            raise RuntimeError("回复服务启动失败，请检查 DSH 连接与配置")
        self.started_at = time.time()
        self.expected_running = True
        atomic_json(
            self.state.home / "services.json",
            {
                "pids": {k: p.pid for k, p in self.children.items()},
                "started_at": self.started_at,
            },
        )
        return self.status()

    def _spawn(self, name, args):
        log = self.state.home / (name + ".log")
        if log.exists() and log.stat().st_size > 2_000_000:
            log.replace(log.with_suffix(".previous.log"))
        with log.open("ab") as stream:
            env = dict(os.environ)
            if not getattr(sys, "frozen", False):
                env["PYTHONPATH"] = (
                    str(Path(__file__).resolve().parents[1])
                    + os.pathsep
                    + env.get("PYTHONPATH", "")
                )
            process = subprocess.Popen(
                args,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=Path.home(),
                env=env,
            )
        log.chmod(0o600)
        self.children[name] = process

    def stop(self):
        with self.lock:
            self.expected_running = False
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
            atomic_json(
                self.state.home / "services.json", {"pids": {}, "started_at": None}
            )
            return {"running": False}

    def status(self):
        with self.lock:
            processes = {
                k: {"pid": p.pid, "alive": p.poll() is None}
                for k, p in self.children.items()
            }
        result = {
            "running": bool(processes) and all(p["alive"] for p in processes.values()),
            "processes": processes,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "restarts": {k: len(v) for k, v in self.restarts.items()},
        }
        try:
            reader = Config.load(self.state.home / "config/reader.toml")
            result["reader"] = LocalAPI(
                reader.port, reader.state_dir / "api-token.txt"
            ).request("/v1/status")
        except (OSError, RuntimeError, ValueError):
            result["reader"] = {"state": "stopped"}
        try:
            from .reply import ReplyConfig

            cfg = ReplyConfig.load(self.state.home / "config/reply.toml")
            with sqlite3.connect(
                (cfg.state_dir / "tasks.sqlite3").as_uri() + "?mode=ro", uri=True
            ) as db:
                result["worker"] = {
                    k: json.loads(v)
                    for k, v in db.execute(
                        "SELECT key,value FROM meta WHERE key IN ('last_success','last_error','cursor')"
                    )
                }
        except (OSError, ValueError, sqlite3.Error):
            result["worker"] = {}
        return result

    def _watch(self):
        while not self.shutdown_event.wait(5):
            try:
                self._watch_once()
            except (OSError, ValueError, RuntimeError):
                self.last_error = "后台维护遇到错误，请查看数据目录权限和剩余空间"

    def _reader_ready(self):
        try:
            config = Config.load(self.state.home / "config/reader.toml")
            return (
                LocalAPI(config.port, config.state_dir / "api-token.txt")
                .request("/v1/status")
                .get("state")
                == "ready"
            )
        except (OSError, RuntimeError, ValueError):
            return False

    def _watch_once(self):
        with self.lock:
            for name in ("reader", "worker"):
                log = self.state.home / (name + ".log")
                if log.exists() and log.stat().st_size > 2_000_000:
                    with log.open("rb") as f:
                        f.seek(-1_000_000, 2)
                        tail = f.read()
                    previous = log.with_suffix(".previous.log")
                    previous.write_bytes(tail)
                    previous.chmod(0o600)
                    with log.open("r+b") as f:
                        f.truncate(0)
                p = self.children.get(name)
                if self.expected_running and p and p.poll() is not None:
                    if name == "worker" and not self._reader_ready():
                        # Reader initialization can take several scans. Do not spend the
                        # worker's restart budget on an expected dependency outage.
                        continue
                    self.restarts[name] = [
                        t for t in self.restarts[name] if time.time() - t < 600
                    ]
                    if len(self.restarts[name]) >= 3:
                        self.last_error = (
                            f"{name} 在十分钟内重复退出，已停止自动重启，请检查配置"
                        )
                        self.expected_running = False
                        break
                    self.restarts[name].append(time.time())
                    config = (
                        self.state.home
                        / "config"
                        / ("reader.toml" if name == "reader" else "reply.toml")
                    )
                    args = (
                        command("--config", str(config), "serve")
                        if name == "reader"
                        else command("reply", "--config", str(config), "run")
                    )
                    self._spawn(name, args)
