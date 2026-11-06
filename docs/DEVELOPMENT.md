# 开发、测试与发布

## 环境准备

Mac 开发使用 Python 3.12 或以上、uv、Node.js 22 或以上、SQLCipher 和 Apple 开发者工具。优先复用本机已经配置的工具。缺少基础工具时通过本机包管理器安装，首次配置 Apple 开发者工具按系统提示完成。

```sh
uv run python scripts/dev.py bootstrap --native
uv run python scripts/dev.py doctor --native
```

`bootstrap` 根据锁文件同步项目 Python 依赖、构建依赖和 Node 测试依赖，安装对应版本的测试浏览器，并可构建 Swift 程序。Mac 缺少 uv 或 SQLCipher 时复用已有 Homebrew 安装。Node.js 与 Apple 开发者工具需先就绪。

Linux 开发使用系统 SQLCipher 动态库、Node.js 和 uv，省略 `--native`。浏览器需要宿主系统的运行依赖；CI 通过 Playwright 的 `--with-deps` 安装。原生 Mac 程序在 Mac 上构建与测试。

## 独立开发服务

```sh
uv run python scripts/dev.py up
uv run python scripts/dev.py emit --text '培训资料什么时候发送？'
uv run python scripts/dev.py down
```

启动结果会给出本地浏览器地址。该环境使用临时数据目录、合成加密数据库和本地 Agent、飞书协议服务，便于 AI 自主启动、注入消息、检查结果与关闭服务。`scripts/dev.py accept` 可以自动完成一次接入、投递和重启验收。

源码生产入口使用 `uv run notebridge desktop`。首次运行原生凭据工具前按 [安装说明](INSTALL.md) 构建并签名。独立开发配置可设置 `NOTE_BRIDGE_HOME`，开发目录应与个人账号数据分离。

## 结构与修改规则

- `wx_watcher/domain/` 保存领域规则。
- `wx_watcher/application/` 保存用例、Worker 和依赖接口。
- `wx_watcher/infrastructure/` 保存具体 Agent、HTTP、任务与桌面资源适配器。
- `wx_watcher/composition.py` 组装生产依赖。
- `wx_watcher/assets/` 保存本地界面。
- `native/Sources/` 保存凭据、取钥、恢复与 Mac 桌面程序。
- `integrations/dsh/` 保存 DSH 桥接的规范源。
- `tests/unit/`、`tests/support/`、`tests/browser/`、`tests/e2e/` 保存不同层级的测试。

模块边界与兼容策略见 [架构](ARCHITECTURE.md)。Agent 扩展见 [提供方接口](AGENT_PROVIDERS.md)。每个功能配套单元测试，新增命令和测试应更新 `tests/features.json`。

## 统一检查

```sh
uv run python scripts/check.py
# Mac 上启用实际隔离 LLDB 测试
uv run python scripts/check.py --lldb
```

完整检查包括行与分支覆盖率、Python 单元与集成测试、Swift 测试、DSH 契约测试、界面测试、浏览器完整流程、源码分发与隐私检查。范围、门槛、分层入口和输出报告见 [测试说明](TESTING.md)。

CI 按 PR 执行两组检查，并取消同一 PR 的过期运行。Checks 的触发范围限定为 PR 与手动运行。发行构建按标签或手动启动，保留必要的构建前检查。测试报告保留 7 天。

## 应用构建

```sh
uv run python scripts/build_macos.py
uv run python scripts/dev.py package-check --backend dist/NoteBridge.app/Contents/Resources/backend/notebridge
```

构建 Swift 两个入口，冻结 Python 后端，收集 SQLCipher 与其动态库和许可证，
创建 Mac 应用、压缩包、DMG 与 SHA-256。原始构建目录不会加入仓库。
PyInstaller 的构建依赖在 uv.lock 中锁定。构建器扫描 Mach-O 最低系统版本，
取所有依赖的最大值写入应用和发布清单，不能只根据 Swift 的 macOS 14 目标声明兼容。
本机 0.3.0 构建的实际最低版本为 macOS 27.0。

本地无 Developer ID 时采用临时签名，清楚标记未公证。
正式签名通过构建环境的 `NOTE_BRIDGE_CODESIGN_IDENTITY` 提供；公证凭据只放维护者本机或
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


打包时若主应用正在运行，构建器拒绝覆盖它。可设置 `NOTE_BRIDGE_DIST_DIR` 指向独立输出目录，
例如项目 `build/releases.noindex/0.3.1`，不影响正在使用的产物。临时 DMG 目录使用 `.noindex` 后缀，
打包完成即清理；可选 Setup 只保留 ZIP，DMG 和本地默认输出各只留主应用，避免重复进入系统应用列表。
