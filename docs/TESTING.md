# 测试体系与自动验收

## 一键运行

在已有 uv、Node.js、SQLCipher 的开发机器上运行以下命令。Mac 原生构建另需可用的 Apple 开发者工具。

```sh
uv run python scripts/dev.py bootstrap --native
uv run python scripts/dev.py doctor --native
uv run python scripts/check.py --lldb
```

Linux 开发环境先安装 `uv`、Node.js 和系统 SQLCipher 开发包，运行 `bootstrap` 时省略 `--native`。安装依赖与权限要求见 [开发环境](DEVELOPMENT.md)。

统一检查遇到失败即停止，返回非零退出码，并保存报告。可以用 `--list` 查看检查项目，用 `--profile core` 或 `--profile ui` 分别运行 CI 的两个分组。

## 测试金字塔

| 层级 | 入口 | 验证内容 |
| --- | --- | --- |
| Python 单元 | `scripts/verify.py --layer unit --coverage` | 领域规则、应用用例、内存端口、协议转换、依赖方向与功能清单 |
| Swift 单元 | `swift test --package-path native -c release` | 凭据、备份、取钥与恢复规则、可信网页与原生桥接 |
| Python 集成 | `scripts/verify.py --layer integration` | SQLCipher/WAL、SQLite 事务、本机 HTTP、真实读取和回复子进程、恢复与幂等 |
| DSH 契约 | `node --test tests/dsh/bridge.test.mjs` | DSH 桥接与宿主 API 的任务、会话、隔离和费用行为 |
| 界面交互 | `npm test` | 引导、账号切换、输入保留、权限帮助与不同宽度下的布局 |
| 完整浏览器流程 | `npm run test:e2e` | 浏览器操作实际后端，从合成加密库新增消息到 Agent、投递和精确回读 |
| 打包程序 | `scripts/dev.py package-check --backend <构建后的程序路径>` | 实际冻结的读取和回复子进程、合成消息草稿与重启去重 |

普通 Python 集成检查必须找到 SQLCipher 和动态库，缺少时直接失败。Python 运行报告中出现跳过也会使检查失败。

Swift 默认显式禁用 4 个可选 LLDB 测试。Mac 上的 `scripts/check.py --lldb` 会启用它们，实际编译并调试临时测试程序。真实微信的授权、重签与登录仍单独验收。

## 功能与测试对应

`tests/features.json` 是维护中的功能清单，列出账号、凭据、取钥、设置、Agent、投递、运行、采集、回复、人工复核、业务资料、桌面界面、开发环境与架构规则。

每项功能必须有 Python 或 Swift 单元测试，并列出集成、浏览器或人工验证范围。单元测试检查清单中的测试存在，以及每个应用操作恰好有一个功能归属。新增功能或命令应同步更新清单与测试。

清单负责追踪功能覆盖，覆盖率负责发现未执行代码。测试设计还需要覆盖失败、重试、并发边界和旧数据兼容。

## 覆盖率门槛

门槛保存在 `pyproject.toml`，由统一检查执行。

| 范围 | 行覆盖率 | 分支覆盖率 |
| --- | --- | --- |
| 领域层 | 至少 95% | 至少 90% |
| 应用层 | 至少 90% | 至少 85% |
| 全部第一方 Python | 至少 80% | 至少 70% |

完整 Python 检查合并子进程覆盖率，覆盖实际工作进程。上游 `_vendor` 源码从第一方指标中排除，Swift 与 JavaScript 通过各自测试入口验证。独立单元检查执行领域和应用层门槛，独立集成检查只报告对应执行情况，完整检查执行全部门槛。

## AI 可独立操作的开发环境

```sh
uv run python scripts/dev.py up
uv run python scripts/dev.py status
uv run python scripts/dev.py emit --text '培训资料什么时候发送？'
uv run python scripts/dev.py down
uv run python scripts/dev.py accept
```

`up` 创建独立的临时数据目录，启动实际本地界面后端、SQLCipher 合成数据源、读取器、回复 Worker、Agent 协议服务和飞书协议服务，并输出可打开的本机地址。`status` 和 `down` 根据本项目的开发环境记录操作它启动的实例。`accept` 每次新建独立子进程，配置并测试完整流程，检查重启后无重复调用或投递，然后关闭实例。

默认使用真实 SQLCipher 加密数据库与 WAL。`--unencrypted` 仅用于明确选择普通 SQLite 的开发调试。开发服务位于 `tests/support/`，其注入消息与控制接口只由开发入口提供。

所有内容均为合成数据，模型与飞书由本地协议服务响应。完整浏览器测试通过真实应用路由和 Worker 运行；界面交互测试另外使用可控的状态夹具，方便覆盖异常页面。

## 真实账号验收边界

系统管理员确认、完整磁盘访问和手机端登录确认需要操作者完成。新的 OS、微信构建或签名需要在对应 Mac 上验证。

真实验收顺序为首次取钥并恢复官方客户端、验证并保存凭据、读取本地会话、选择测试会话、连接实际 Agent、确认模型与费用记录、选择测试飞书群、触发新增消息并核对原文、建议和接收时间。保留脱敏结果与版本信息。

模拟渠道的完整 E2E 通过后，发布记录还应单列真实账号、模型和飞书的验收状态。当前开发自动化覆盖权限以后的业务过程与隔离的原生规则。

## 报告与失败诊断

- `output/checks/summary.json` 记录各检查阶段、退出码与耗时，阶段日志保存在同目录。
- `output/verification.json` 记录 Python 各层数量、失败、跳过和覆盖率结果。
- `output/coverage/coverage.json`、`coverage.xml`、`html/` 提供可追溯到测试上下文的覆盖率明细。
- `output/checks/development-failures/` 保存有界的启动失败诊断。
- 开发环境输出私有临时目录中的 `server.log`。开发实例记录位于 `.runtime/dev/`。

报告、运行数据与数据库均在忽略目录中。公开问题和 PR 使用脱敏摘要。
