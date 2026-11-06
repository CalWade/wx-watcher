"""Compatibility entry point and CLI for the composed reply worker."""

import argparse
import fcntl
import json
import os
import signal
import subprocess as subprocess
import threading
from pathlib import Path

from .application.replies import ReplyWorker
from .domain.replies import feishu_text as feishu_text
from .domain.replies import hash_id as hash_id
from .domain.replies import make_prompt as make_prompt
from .domain.replies import parse_proposal as parse_proposal
from .infrastructure.agents.dsh import DshGateway
from .infrastructure.clock import SystemClock
from .infrastructure.http import LocalAPI as LocalAPI
from .infrastructure.reply_config import ReplyConfig as ReplyConfig
from .infrastructure.tasks import ReplyStore as ReplyStore


def now():
    return SystemClock().timestamp()


class ReplyAgent(ReplyWorker):
    def __init__(self, config, **dependencies):
        from .composition import build_reply_dependencies

        ports = build_reply_dependencies(config, **dependencies)
        super().__init__(config, **ports)

    @property
    def dsh(self):
        return self.agent.client

    @dsh.setter
    def dsh(self, client):
        self.agent = DshGateway(client)


def main():
    parser = argparse.ArgumentParser(
        description="WeChat → Agent → Feishu reply suggestions"
    )
    parser.add_argument("--config", type=Path, default=Path("config/reply.local.toml"))
    parser.add_argument(
        "command", choices=["status", "run", "once", "drafts", "dry-run"]
    )
    parser.add_argument("--id", help="Task ID for dry-run")
    args = parser.parse_args()
    agent = ReplyAgent(ReplyConfig.load(args.config))
    if args.command == "status":
        print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
    elif args.command == "drafts":
        print(
            json.dumps(
                agent.store.tasks(["draft", "needs_review", "delivery_review"], 20),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "dry-run":
        task = agent.store.task(args.id) if args.id else None
        if task is None:
            raise ValueError("Specify an existing draft ID")
        print(json.dumps(agent.send(task, dry_run=True), ensure_ascii=False, indent=2))
    else:
        lock = (agent.config.state_dir / "worker.lock").open("a+")
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "A reply worker is already running for this state directory"
            ) from None
        agent.initialize()
        if args.command == "once":
            agent.tick()
            print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
        else:
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            print(
                json.dumps(
                    {
                        "started": now(),
                        "mode": "send_to_feishu"
                        if agent.config.send_enabled
                        else "draft_only",
                    }
                ),
                flush=True,
            )
            while not stop.is_set():
                try:
                    agent.tick()
                except (OSError, RuntimeError, ValueError) as exc:
                    with agent.store.db:
                        message = (
                            str(exc)[:300]
                            if isinstance(exc, RuntimeError)
                            else type(exc).__name__
                        )
                        agent.store.set_meta("last_error", message)
                    print(
                        json.dumps({"at": now(), "error": type(exc).__name__}),
                        flush=True,
                    )
                stop.wait(agent.config.poll_interval)
        lock.close()
    agent.store.db.close()
    return 0


if __name__ == "__main__":
    main()
