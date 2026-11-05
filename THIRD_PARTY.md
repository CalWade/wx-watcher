# Third-party sources

## GreenBubbles / wcdb-key-tool

- Source: https://github.com/bojieli/greenbubbles
- Pin: `69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b`
- MIT license: [native/Vendor/LICENSE.greenbubbles](native/Vendor/LICENSE.greenbubbles).
- Vendored acquisition sources/tests only; unrelated history, sending and perception
  components are excluded.
- Upstream credits https://github.com/TANGandXUE/wcdb-key-tool for the capture method.

Local changes: configurable LLDB path, stop-on-exec/quit settings, genuine setup
milestones, 30-second setup deadline, detach-first cleanup, SIGPIPE protection,
stricter KDF parameter filtering, cancellation, and line-based ordering tests.
NoteBridge's Keychain bridge, encrypted backup, privileged recovery transaction,
desktop shell and application orchestration are separate project code.

## wechat-decrypt

- Source: https://github.com/freanocank/wechat-decrypt
- Pin: `f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd`
- MIT, Copyright (c) 2026 cocofeng.
- License: [wx_watcher/_vendor/wechat_decrypt/LICENSE](wx_watcher/_vendor/wechat_decrypt/LICENSE).

| File | Upstream | Change |
| --- | --- | --- |
| wal_snapshot.py | scripts/common/wal_snapshot.py | Unchanged |
| appmsg.py | appmsg.py | Unchanged |
| message.py | message.py | Decode subset; removed DB imports and sender inference |

Sender IDs are resolved in each shard's Name2Id namespace. No upstream installer,
Frida extraction or MCP-registration script is executed.

## SQLCipher / OpenSSL

https://github.com/sqlcipher/sqlcipher — BSD-style license.
https://www.openssl.org — retain the license of the version used by the build.
Source users install their engine. Mac bundles collect the resolved engine, dynamic
dependencies and notices. System frameworks are not redistributed.

## Python and application packaging

zstandard provides local message decompression. Versions and source integrity are
pinned in uv.lock. PyInstaller is a build dependency; its installed notices and
those of redistributed dependencies are collected for app distribution.

## External integrations

- DSH: https://github.com/deepseek-ai/deepseek-harness (MIT).
- cost-meter: https://github.com/Han-1413141/dsh-cost-meter (MIT).
- Lark CLI: https://github.com/larksuite/cli.

External application bundles, private profiles, account data and credentials are
not redistributed with this repository.

## Additional runtime notices

Mac artifacts include the Python 3.12 license (copied from the installed CPython
3.12.13 distribution), zstd 1.5.7 BSD license, and the installed notices for
liblzma (XZ, 0BSD), mpdecimal (BSD), OpenSSL and SQLCipher. SQLite is in the public
domain: https://sqlite.org/copyright.html. The Python license also credits its
included components. Original license text is retained in `licenses/` and each
application's `Contents/Resources/Notices/`; PyInstaller includes its bootloader
exception. System macOS frameworks remain OS-provided.

Primary sources: https://github.com/python/cpython/blob/v3.12.13/LICENSE and
https://github.com/facebook/zstd/blob/v1.5.7/LICENSE.

## Full Disk Access onboarding reference

https://github.com/inket/FullDiskAccess documents native metadata probing to have
macOS register an unchecked Full Disk Access entry. The launcher uses a native protected-path probe; it opens and closes the system
TCC file without reading its contents or changing the TCC database. The actual
WeChat directory is checked independently. Actual WeChat data access is verified separately.
System registration behavior still depends on macOS, and permission is enabled
only by the user.
