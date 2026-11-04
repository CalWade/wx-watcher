from __future__ import annotations

import argparse
import json
import re
import shutil
import signal
import threading
from pathlib import Path

from .api import create_server, load_token
from .collector import Collector
from .config import Config, discover_root
from .source import load_master
from .store import Store


def doctor(config: Config) -> dict:
    result = {
        "backend": config.backend,
        "group_name": config.group_name,
        "writes_wechat": False,
        "captures_screen": False,
        "attaches_to_process": False,
        "sends_wechat": False,
    }
    try:
        root = discover_root(config.db_root)
        result["data_access"] = "ok"
        result["message_shards"] = sum(
            1
            for path in (root / "message").iterdir()
            if path.is_file() and re.fullmatch(r"message_\d+\.db", path.name)
        )
    except (OSError, ValueError) as exc:
        result["data_access"] = "blocked"
        result["data_error"] = str(exc)
    if config.backend == "sqlcipher":
        result["sqlcipher"] = shutil.which(config.sqlcipher)
        try:
            if config.key_ref:
                from .credentials import load_reference

                load_reference(config.key_ref, config.keytool)
            else:
                load_master(config.key_file)
            result["key"] = "present_format_valid_not_yet_database_verified"
        except (OSError, ValueError) as exc:
            result["key"] = "missing_or_invalid"
            result["key_error"] = str(exc)
    result["ready_for_live_read"] = result["data_access"] == "ok" and (
        config.backend == "sqlite"
        or bool(result.get("sqlcipher"))
        and result.get("key", "").startswith("present_")
    )
    return result


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "reply":
        from .reply import main as reply_main

        del sys.argv[1]
        return reply_main()
    if len(sys.argv) > 1 and sys.argv[1] in {"desktop", "setup", "service", "keytool"}:
        from .desktop import main as desktop_main

        return desktop_main()
    parser = argparse.ArgumentParser(description="微信本地数据库只读服务")
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("command", choices=["doctor", "once", "serve", "token"])
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "doctor":
        result = doctor(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready_for_live_read"] else 2
    if args.command == "token":
        print(load_token(config.state_dir))
        return 0
    store = Store(config.state_dir)
    collector = Collector(config, store)
    if args.command == "once":
        try:
            collector.poll()
            print(
                json.dumps(
                    {"status": collector.status(), "messages": store.history(10)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(
                json.dumps({"state": "blocked", "error": str(exc)}, ensure_ascii=False)
            )
            return 2
        finally:
            store.close()
    server = create_server(store, collector, load_token(config.state_dir), config.port)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    collector.start()
    print(
        json.dumps(
            {
                "api": f"http://127.0.0.1:{server.server_port}",
                "group": config.group_name,
                "mode": "read_only_database",
                "token_file": str(config.state_dir / "api-token.txt"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        collector.stop()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
