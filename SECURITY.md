# Security and privacy

Use NoteBridge only with accounts and conversations you are authorized to process.

- The daily reader does not attach to WeChat, modify its files or send messages.
- Initial acquisition is separate, can re-sign/debug the client, and needs explicit
  administrator authorization, a verified backup and official-client recovery.
- Secrets must not enter model prompts, HTTP responses, logs, process arguments,
  diagnostic exports or Git history. Keychain references are not secret values.
- Native internal key access is local, not an HTTP endpoint.
- Local APIs bind to loopback, require authentication and reject foreign hosts and
  origins. Do not expose them through public proxies.
- Model output is untrusted. Tools are removed from requests and denied by a guard.
  Reply JSON is validated; the destination and sending identity are fixed.
- Uncertain delivery requires inspection rather than indefinite retries.

Encrypted snapshots, plaintext tasks and DSH sessions are private local data.
Selected messages and business passages go to the configured model provider;
suggestions go to the configured Feishu group. DSH/plugins have their own retention.

Report vulnerabilities through GitHub private reporting when available. Otherwise,
open a minimal issue asking for a private channel without sharing customer data.

A login-password change does not reliably revoke a copied database key and matching
database. Remove exposed copies and investigate downstream storage.

Ad-hoc/unsigned app builds are developer/test distributions. Notarized release
requires maintainer-owned Apple credentials. Do not disable SIP or Gatekeeper as
an installation shortcut.
