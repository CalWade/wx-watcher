# DSH 与 dsh-cost-meter 接入

## 前提

安装并正常打开 DSH，在 DSH 自己的设置中配置一个可以真实回答的模型渠道。
项目不会安装个人专用渠道、复制模型 API Key 或改变全局默认模型。
完整推荐路径需要 dsh-cost-meter 已安装、加载，并能提供会话费用服务。

当前真实验证版本为 DSH NEXT 2.0.19-next、cost-meter 1.9.16；其他 DSH 版本
必须通过能力检查和真实调用测试，不能只看版本号相近就声明兼容。

## 操作步骤

1. 确认 DSH 的模型配置正常。第 3 步的「连接 DSH 与费用插件」会复用费用插件；缺少时尝试通过已安装 Desktop 的官方安装入口补齐。
2. 打开 NoteBridge →「接入设置」→ 第 3 步「接入模型」。手动更新桥接的入口放在「手动维护桥接插件」中。
3. 安装器只添加或更新自己拥有的 `notebridge` 条目，并备份原 patch 到私有目录。
4. 确认桥接状态 ready；若没有就绪，在 DSH 插件日志检查缺失服务。
5. 点击「测试模型与记账」。此操作会产生一次真实模型用量。
6. 成功结果应有建议内容、对应 session_id、Token 用量和 cost-meter 会话记录。

默认 profile 为 `~/.dsh/profiles/desktop`，数据为 `~/.dsh/storages/notebridge`。
如果使用其他 DSH_HOME，给 DSH 和 NoteBridge 设置相同的环境变量。
原生宿主 SDK 优先从当前 DSH 运行环境解析；特殊安装可配置 `DSH_HOST_PACKAGE_JSON`。

## 运行契约

桥接监听本机 8766，所有请求要求私有 Bearer token。
Python worker 使用稳定 job ID；相同 ID 和相同请求复用结果，不会重复创建模型会话。
同 ID 不同内容返回 409。队列满返回 429。中断中的请求不会悄悄再跑一次。

Agent 使用当前默认渠道。模型看不到执行工具，工具调用也有拒绝守卫。
最终请求头记录实际工具数；会话和费用写入后才释放会话。
费用估算依据 cost-meter 价格配置，不等于供应商实际扣款。

## 更新与回退

桥接采用按源文件 SHA 分目录的版本，避免 ESM 缓存继续运行旧代码。
更新只停用本条目，再启用新文件；不会主动退出其他 DSH 会话。
如需移除，只删除自己的 `notebridge` 条目。不要用历史完整配置备份覆盖后来的用户修改。
会话与费用账本保留，由 DSH 自己管理。

## 常见故障

| 现象 | 排查 |
| --- | --- |
| 无法找到 SDK | 核对实际 DSH 安装；特殊位置设置 DSH_HOST_PACKAGE_JSON |
| 插件已安装但不 ready | 检查 costMeter、agents、sessionProjectionCache 等宿主服务 |
| 模型失败 | 到对应 DSH 会话查渠道错误，不要在公开 Issue 粘贴请求转储 |
| 没有费用 | 区分缺少 provider usage、插件没加载、价格未知；不能把未知价格记成免费 |
| DSH 退出 | 保留任务，重开 DSH 后继续查询；中断的调用需要检查原会话 |

## 从空白 DSH 环境安装 cost-meter

已有插件且测试正常时继续复用，不需要为了 NoteBridge 升级。没有插件时：

1. 从 [DSH 官方 Releases](https://github.com/deepseek-ai/deepseek-harness/releases)
   安装 Desktop，至少正常启动一次，让它创建 desktop Profile。
2. 在 Desktop 提供的命令行安装入口安装其自带 `dsh` 命令；不要用另一个全局 DSH
   命令修改 Desktop 的依赖。完全退出 Desktop 后安装插件。
3. 按 [cost-meter 的 Desktop 安装说明](https://github.com/Han-1413141/dsh-cost-meter#installation)
   选择与宿主兼容的版本。本项目曾实际验证 1.9.16，示例固定该版本：

   ```sh
   dsh plugin --profile desktop add dsh-cost-meter@1.9.16
   ```

4. 重新打开 Desktop，在费用设置确认插件加载。需要 pnpm 时复用现有兼容版本；
   包管理器报错应修复真实环境，不修改其他模型渠道。
5. 先在 DSH 普通会话问一个简短问题，确认能返回内容且费用面板有相应记录。
6. 再打开 NoteBridge 安装桥接并点击模型测试。若改用新 DSH / cost-meter 版本，
   重新验证宿主服务契约与记账；上面的固定版本不是对新版宿主的兼容承诺。

桥接正常测试至少应满足：ready=true、tools=0、cost_meter.found=true。
提示词和模型结果会保存在 DSH 的会话及桥接私有任务中，需按自己的数据保留策略管理。
损坏的桥接任务文件会隔离到 quarantine；不会阻止其他任务启动，也不会当作成功结果。


当前自动安装适配已验证 Desktop 的 `lib/plugin-cli.js` 入口，固定 cost-meter 1.9.16。
已安装的版本不会降级，也不会覆盖费用账本。若宿主入口已变化或包管理器失败，界面提示后停止；
可按上面的手动备用步骤处理，不能在未知版本上自动修改全局模型设置。
