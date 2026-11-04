# WX Watcher

A local-first macOS assistant: read selected personal WeChat conversations from
local encrypted databases, draft replies through your existing DSH model channel,
and deliver suggestions to an internal Feishu group for human review.

- Desktop/menu-bar app and local setup console.
- Independent key acquisition, verification and macOS Keychain storage.
- Read-only encrypted DB/WAL snapshots and authenticated local API.
- Durable cursors, message batching and cautious delivery recovery.
- DSH integration with no model tools, reusing dsh-cost-meter.
- Feishu app-bot API or existing Lark CLI configuration.

The daily reader never sends to WeChat. Initial acquisition can temporarily re-sign
and attach a debugger to WeChat and requires explicit local authorization.
It does not establish zero account-enforcement risk.

```sh
git clone https://github.com/CalWade/wx-watcher.git
cd wx-watcher
uv sync --frozen
swift build --package-path native -c release
codesign --force --sign - native/.build/release/wx-keytool
uv run wx-watcher desktop
```

Install SQLCipher and the appropriate Apple developer tools first.
[Installation](docs/INSTALL.md) · [Acquisition](docs/KEYTOOL.md) · [DSH](docs/DSH.md) ·
[Feishu](docs/FEISHU.md) · [Operations](docs/OPERATIONS.md) · [API](docs/API.md)

The [README](README.md) lists actually tested versions. Other builds need explicit
qualification. Selected context goes to your model provider, and suggestions go to
your configured Feishu group. Never commit keys, customer records or unreviewed logs.

[MIT](LICENSE) · [Sources](THIRD_PARTY.md) · [Security](SECURITY.md)
