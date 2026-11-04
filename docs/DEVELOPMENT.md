# 开发、测试与发布

## 仓库结构

- `wx_watcher/`：读取、凭据访问、任务、集成、控制台后端。
- `wx_watcher/assets/`：本地界面，无外部 CDN。
- `native/Sources/WXKeytool`：Keychain、备份、取钥与恢复事务。
- `native/Sources/WXWatcherApp`：Mac 窗口、WebView、菜单栏、原生选择器。
- `native/Vendor`：固定版本的上游取钥子集、原始许可。
- `integrations/dsh/`：DSH 桥接的规范源。
- `tests/`：合成数据与 API/恢复测试。
- `scripts/`：构建、验收、发布检查。
- `docs/`：安装、使用、集成、维护与开发文档。

## 准备

```sh
uv sync --frozen --group build
swift build --package-path native -c release
codesign --force --sign - native/.build/release/wx-keytool
uv run wx-watcher desktop
```

独立开发数据可设置 `WX_WATCHER_HOME`。不要共用正在验证的真实账号任务目录。
不要把当前机器的绝对路径、账号 ID、机器人 ID 或私有日志加入测试。

## 自动检查

```sh
uv run python scripts/verify.py
swift test --package-path native -c release
node --check wx_watcher/assets/app.js
node --test tests/dsh/bridge.test.mjs
npm ci
npm test
uv run ruff check wx_watcher scripts tests
uv run ruff format --check wx_watcher scripts tests
uv run python scripts/check_release.py
```

Python 测试通过原生 SQLCipher 生成合成加密数据库和活动 WAL；不用私人账号。
Swift 测试覆盖取钥脚本、KDF/HMAC、秘密文件权限、解析、输入与凭据边界。
不在 CI 自动重签或附加真实微信。图形交互通过独立浏览器会话验证本地界面。

## 应用构建

```sh
uv run python scripts/build_macos.py
```

构建 Swift 两个入口，冻结 Python 后端，收集 SQLCipher 与其动态库和许可证，
创建 Mac 应用、压缩包、DMG 与 SHA-256。原始构建目录不会加入仓库。
PyInstaller 的构建依赖在 uv.lock 中锁定。构建器扫描 Mach-O 最低系统版本，
取所有依赖的最大值写入应用和发布清单，不能只根据 Swift 的 macOS 14 目标声明兼容。
本机 0.2.0 构建的实际最低版本为 macOS 27.0。

本地无 Developer ID 时采用临时签名，清楚标记未公证。
正式签名通过构建环境的 `WX_CODESIGN_IDENTITY` 提供；公证凭据只放维护者本机或
GitHub Actions secrets，不进入源码。发布必须核对公证实际结果。

## 真实验收

使用自己的测试账号/会话、模型渠道和飞书测试群。
记录时间戳、版本、实际请求工具数、费用记录、消息 ID 与回读结果；报告留在私有目录。
不要把一次重放耗时当作新微信消息的端到端延迟。
24 小时运行要实际计时；短时测试、模拟时间和先前其他版本运行不能替代。

## 发布

先通过所有检查和隐私审阅，再创建版本标签与发布产物。
检查源码包和应用包文件清单，防止包含 .runtime、work、配置、聊天、证书和密钥。
源码、DMG、校验值、兼容矩阵、签名状态与已知限制必须对应同一提交。
