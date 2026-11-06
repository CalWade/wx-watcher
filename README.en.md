# NoteBridge

The GitHub repository remains **wx-watcher**. See [migration and compatibility](docs/MIGRATION.md) when upgrading from WX Watcher.

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
codesign --force --sign - native/.build/release/notebridge-access
uv run notebridge desktop
```

Install SQLCipher and the appropriate Apple developer tools first.
[First run with UI examples (Chinese)](docs/FIRST_RUN.md) · [Installation](docs/INSTALL.md) · [Acquisition](docs/KEYTOOL.md) · [DSH](docs/DSH.md) ·
[Feishu](docs/FEISHU.md) · [Operations](docs/OPERATIONS.md) · [API](docs/API.md)

The [README](README.md) lists actually tested versions. Other builds need explicit
qualification. Selected context goes to your model provider, and suggestions go to
your configured Feishu group. Never commit keys, customer records or unreviewed logs.

## Development and verification

```sh
uv run python scripts/dev.py bootstrap --native
uv run python scripts/dev.py accept
uv run python scripts/check.py --lldb
```

The autonomous development environment uses synthetic SQLCipher/WAL data and local
Agent and Feishu protocol peers. Coverage gates, feature ownership and dependency
rules run in CI. Initial macOS permissions and actual account login require the operator.

[Architecture](docs/ARCHITECTURE.md) · [Testing](docs/TESTING.md) · [Agent providers](docs/AGENT_PROVIDERS.md)

[MIT](LICENSE) · [Sources](THIRD_PARTY.md) · [Security](SECURITY.md)
