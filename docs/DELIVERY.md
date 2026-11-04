# Delivery and acceptance

The release is a macOS desktop application, a standalone guided key tool, a local
read-only data API, and a DSH-to-Feishu reply worker. The repository is maintained
at https://github.com/CalWade/wx-watcher.

## Required delivery

- Reproducible builds, pinned upstream acquisition source, preserved licenses.
- Native macOS launcher and menu bar; local configuration wizard and control panel.
- Account discovery, validated key import, Keychain storage and key references.
- Capture progress with explicit administrator authorization and client recovery.
- Searchable conversation selection; reader/worker start, stop and status.
- DSH plugin installation, model/cost-meter diagnostics and actual inference test.
- Feishu application-bot/CLI configuration, test send, readback and fixed destination.
- Editable business knowledge and reply rules; durable batching and task recovery.
- Background launch, bounded retries, safe log retention and diagnostic export.
- Chinese installation, capture, DSH, Feishu, operations and developer documentation.
- Native, Python, browser and packaging checks; no personal data in Git artifacts.

## Release gates

1. A fresh configuration can complete the guided setup.
2. Synthetic encrypted DB/WAL tests prove source files are never written.
3. Model requests expose no tools; retries reuse durable jobs.
4. Real WeChat-to-DSH-to-Feishu checks record their actual scope and timings.
5. Restart, unavailable service, lock/sleep and uncertain delivery scenarios are tested.
6. Continuous operation evidence records the actual duration; 24-hour qualification
   must never be claimed from short or simulated tests.
7. Archives contain no keys, private account IDs, chat logs or developer paths.
8. Signing/notarization status and the tested OS/client matrix are explicit.

The application does not send messages to WeChat. Initial acquisition can involve
client re-signing and debugger attachment; these operations require explicit local
authorization. Public distribution cannot promise zero account-enforcement risk.
