"""Product identities and explicit compatibility with the 0.2 release."""

import os
from pathlib import Path

VERSION = "0.4.2"
APP_NAME = "NoteBridge"
BUNDLE_ID = "io.github.calwade.notebridge"
LEGACY_BUNDLE_ID = "io.github.calwade.wx-watcher"
BRIDGE_ID = "notebridge"
LEGACY_BRIDGE_ID = "wx-reply-agent"


def app_home() -> Path:
    override = os.environ.get("NOTE_BRIDGE_HOME") or os.environ.get("WX_WATCHER_HOME")
    return (
        Path(override or "~/Library/Application Support/NoteBridge")
        .expanduser()
        .resolve()
    )


def dsh_storage(home: Path | None = None) -> Path:
    home = home or Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
    primary, legacy = home / "storages/notebridge", home / "storages/wx-reply-agent"
    return primary if primary.exists() or not legacy.exists() else legacy


def login_file(home: Path | None = None) -> Path:
    return (home or Path.home()) / f"Library/LaunchAgents/{BUNDLE_ID}.plist"
