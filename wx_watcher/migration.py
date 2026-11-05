"""Move closed application state atomically; preserve references and recovery paths."""

from __future__ import annotations

import fcntl
import json
import time
import uuid
from pathlib import Path

TERMINAL_CAPTURE_PHASES = {"completed", "cancelled", "failed", "recovered"}


def validate_move(legacy: Path, primary: Path) -> bool:
    if legacy.is_symlink():
        if legacy.resolve() == primary.resolve() and primary.is_dir():
            return False
        raise ValueError("旧数据路径指向其他位置，请保留原数据并检查迁移路径")
    if not legacy.exists():
        return False
    if primary.exists() or primary.is_symlink():
        raise ValueError("新旧数据目录同时存在，请先核对数据；不会自动覆盖其中任何一份")
    return True


def move_with_legacy_link(legacy: Path, primary: Path) -> bool:
    if not validate_move(legacy, primary):
        return False
    primary.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(primary)
    try:
        legacy.symlink_to(primary, target_is_directory=True)
    except BaseException:
        primary.rename(legacy)
        raise
    return True


def _finish_migration(legacy: Path, primary: Path, plan: dict) -> dict:
    from .appstate import atomic_json

    if legacy.exists() and not legacy.is_symlink():
        move_with_legacy_link(legacy, primary)
    elif not legacy.exists():
        legacy.symlink_to(primary, target_is_directory=True)
    elif legacy.resolve() != primary.resolve():
        raise ValueError("旧数据兼容路径与迁移目标不一致，请保留两份数据")
    old_setup, new_setup = primary / "keytool", primary / "setup"
    if old_setup.exists() and not old_setup.is_symlink():
        move_with_legacy_link(old_setup, new_setup)
    atomic_json(primary / "settings.json", plan["settings"])
    atomic_json(primary / "branding-migration.json", plan["report"])
    (primary / "branding-migration.pending.json").unlink(missing_ok=True)
    return plan["report"]


def migrate_default_state(
    home: Path | None = None, *, wait_seconds: float = 30
) -> dict:
    from .appstate import atomic_json

    home = home or Path.home()
    base = home / "Library/Application Support"
    legacy, primary = base / "WXWatcher", base / "NoteBridge"
    journal_name = "branding-migration.pending.json"
    pending = next(
        (
            root / journal_name
            for root in [primary, legacy]
            if (root / journal_name).is_file()
        ),
        None,
    )
    if pending:
        lock_root = pending.parent
    elif legacy.is_symlink():
        if primary.is_dir() and legacy.resolve() == primary.resolve():
            return {"migrated": False}
        raise ValueError(
            "旧版数据目录是自定义链接，请先用 NOTE_BRIDGE_HOME 显式指定现有数据目录"
        )
    elif not legacy.exists():
        return {"migrated": False}
    else:
        if primary.exists():
            raise ValueError("新旧应用数据目录同时存在，请先核对；不会自动合并或覆盖")
        lock_root = legacy
    lock = (lock_root / "desktop.lock").open("a+")
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "旧版仍在运行，请等待退出完成后重新连接 NoteBridge"
                    ) from None
                time.sleep(0.1)
        if pending:
            plan = json.loads(pending.read_text())
            if plan["report"]["data_path"] != str(primary) or plan["report"][
                "legacy_path"
            ] != str(legacy):
                raise ValueError("迁移记录的路径不匹配，请保留原数据并检查记录")
            return _finish_migration(legacy, primary, plan)
        jobs = legacy / "keytool/jobs"
        if jobs.exists():
            for directory in jobs.iterdir():
                file = directory / "progress.json"
                while file.exists():
                    value = json.loads(file.read_text())
                    safe = value.get("phase") in TERMINAL_CAPTURE_PHASES or (
                        value.get("phase") == "captured"
                        and value.get("official_restored") is True
                    )
                    if safe:
                        break
                    if (
                        value.get("phase") == "recovery_required"
                        or time.monotonic() >= deadline
                    ):
                        raise RuntimeError(
                            "有尚未完成的取钥或客户端恢复任务，请先完成恢复后重新连接"
                        )
                    time.sleep(0.1)
        value = json.loads((legacy / "settings.json").read_text())
        backup = legacy / "backups" / ("notebridge-migration-" + uuid.uuid4().hex)
        backup.mkdir(parents=True, mode=0o700)
        atomic_json(backup / "settings.before.json", value)
        for filename in ["reader.toml", "reply.toml"]:
            source = legacy / "config" / filename
            if source.exists():
                target = backup / filename
                target.write_bytes(source.read_bytes())
                target.chmod(0o600)
        # Rewrite only known path fields, preserving history, prompts and opaque key references.
        prefix = str(legacy) + "/"
        value["knowledge_files"] = [
            str(primary) + path[len(str(legacy)) :]
            if isinstance(path, str) and path.startswith(prefix)
            else path
            for path in value.get("knowledge_files", [])
        ]
        value["brand_bridge_pending"] = True
        resume = legacy / "migration-request.json"
        if (
            resume.is_file()
            and json.loads(resume.read_text()).get("resume_monitor") is True
        ):
            value["migration_resume_requested"] = True
        report = {
            "migrated": True,
            "legacy_path": str(legacy),
            "data_path": str(primary),
            "backup": str(primary / backup.relative_to(legacy)),
            "legacy_login_installed": (
                home / "Library/LaunchAgents/io.github.calwade.wx-watcher.plist"
            ).is_file(),
        }
        plan = {"settings": value, "report": report}
        atomic_json(legacy / journal_name, plan)
        return _finish_migration(legacy, primary, plan)
    finally:
        lock.close()


def migrate_dsh_storage(home: Path) -> bool:
    """Called only after the previous bridge has relinquished its listening port."""
    return move_with_legacy_link(
        home / "storages/wx-reply-agent", home / "storages/notebridge"
    )
