# Contributing

Describe the concrete behavior, reproduction steps and version context before
large changes. Use the pinned dependencies; see [development](docs/DEVELOPMENT.md).

Follow the [architecture boundaries](docs/ARCHITECTURE.md). Every feature needs
unit tests and a mapping in `tests/features.json`. Agent-specific protocols and SDKs
belong in adapters registered by the composition root.

Run `uv run python scripts/check.py` before submitting. On a development Mac,
`--lldb` also enables isolated debugger tests. See the [test pyramid](docs/TESTING.md)
for coverage gates and reports. Queue and recovery changes need crash and retry
cases. UI changes need interaction checks. Personal-account tests require an
operator and stay outside public CI.

Never submit database keys, actual customer messages, DB/WAL files, private DSH
profiles or unreviewed logs. Prefer synthetic fixtures and minimized diagnostics.

Preserve upstream licenses, pin revisions and record changes in THIRD_PARTY.md.
PRs should explain the trigger, resulting behavior and validation. Clearly state
unverified OS/client combinations. Review archive contents before release.
