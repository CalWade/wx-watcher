# Changelog

## 0.4.1

- Start WeChat suspended under the signed-in macOS user, install the key listener, then allow its login window to run. Remove the manual login-page confirmation from new capture jobs.
- Check debugger setup before resuming; stop on setup failure, process exit or invalid candidates. Clean up stopped processes before restoring the official client.
- Allow up to 90 seconds for debugger setup and retain a 10-minute overall deadline.
- Add opt-in real LLDB tests against a disposable program for first-call capture, cancellation and failed setup; update the first-run guide and UI tests. Real WeChat acceptance of this new launch path is still pending.

## 0.4.0

- Reduce primary navigation to Workbench and Setup; move knowledge, credential maintenance and diagnostics into More.
- Guide setup through five focused steps with one visible form; keep first-time capture separate from everyday monitoring.
- Replace the dense green dashboard with consistent neutral surfaces, spacing, responsive forms and explicit action hierarchy.
- Preserve expanded account details and focused inputs during background refresh; wait for refreshed state before completing saves.
- Resume capture progress when returning to its page, restore retry controls after cancellation, and prevent concurrent credential import.
- Use in-page confirmation dialogs in both browser and native WebView, and show empty search results directly in the active step.
- Connect DSH and cost-meter without modifying unrelated WeChat or Feishu settings. Show custom Feishu credentials immediately after transport selection.
- Clear the old Feishu destination and automatic delivery when switching transport or application identity.
- Remove the permanent native permission footer; show a separate closable helper only when requested, and set a minimum usable native window size.

## 0.3.1

- Always show the current WeChat login, selected project account and saved-credential status separately.
- Connect an unsaved current account directly to its own acquisition target; verify saved keys before selecting an account.
- Block mismatched/unconfirmed accounts before conversation reads, monitoring or full-chain tests; pause supervised services when account identity changes.
- Prevent auto-connect from silently reusing another account's credentials; reset conversation and automatic delivery options when changing accounts.
- Preserve old credentials and records, show saved accounts even when data access is denied, and keep the optional Setup in its separate ZIP.
- Leave only the main app in the DMG/output and remove temporary staging copies; support isolated build output and reject overwriting a running app.

## 0.3.0

- Put first-time WeChat connection on the home page, with explicit prerequisite checks, login timing and a verified-completion handoff.
- Distinguish missing local data from privacy denial; check real debugger binaries without launching the system installer shim.
- Add a fresh-machine guide with synthetic UI previews and automated first-run tests.

- Product, app bundles, executables, login registration and DSH bridge use NoteBridge; GitHub repository remains wx-watcher.
- Migrate closed application state and bridge storage while preserving account references, cursors, task records and cost-meter history.
- Read legacy Keychain entries and encrypted backups; copy approved credentials into the new namespace without recapturing keys.
- Journal interrupted migrations, retain compatibility links and stop on data conflicts or unfinished client recovery.


## 0.2.0

- Mac launcher/menu bar and authenticated local setup console.
- Automatic account/credential reuse, DSH/cost-meter preparation and combined connection checks.
- Native privacy registration probe, single-page login options and available-port selection.
- Standalone key tool, Keychain references and encrypted credential backup.
- Pinned acquisition code, explicit progress, cancellation and recovery.
- Check native data/app-management permissions before capture; retain precise preparation failures after restoration.
- Separate login-page confirmation from login readiness; stop and restore on a detected account-directory change after candidate rejection.
- Ignore old-account cache handles when identifying the account currently open in WeChat.
- Conversation selection, local knowledge passages and reply rules.
- DSH bridge deployment and model/cost-meter diagnostics.
- Feishu API transport alongside existing Lark CLI support.
- Durable batching and daily model-call limits.
- Bounded service supervision with reader readiness gating, retained cursors and explicit task review/retry.
- Per-application macOS data access diagnostics and native privacy-settings entry.
- App build with dependency-derived OS floor, CI and installation/maintenance/developer documentation.
- Verified the installed credential wizard's fresh capture, 19/19 database validation, official-client restoration and Keychain readback on the pinned Mac environment.

Release qualification is recorded separately. This list is not proof of every
platform/client combination or a full 24-hour run.

## 0.1.0

- Read-only encrypted DB/WAL API, persistent events, DSH and Feishu test integration.
