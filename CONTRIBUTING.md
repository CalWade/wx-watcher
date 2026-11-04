# Contributing

Describe the concrete behavior, reproduction steps and version context before
large changes. Use the pinned dependencies; see [development](docs/DEVELOPMENT.md).

Run Python tests, native tests, JavaScript syntax checks and release checking.
Queue/recovery changes need crash/retry tests; UI changes need interaction checks.
Personal-account tests must not run in public CI.

Never submit database keys, actual customer messages, DB/WAL files, private DSH
profiles or unreviewed logs. Prefer synthetic fixtures and minimized diagnostics.

Preserve upstream licenses, pin revisions and record changes in THIRD_PARTY.md.
PRs should explain the trigger, resulting behavior and validation. Clearly state
unverified OS/client combinations. Review archive contents before release.
