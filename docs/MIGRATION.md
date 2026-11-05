# 从 WX Watcher 0.2 升级到 NoteBridge 0.3

产品统一使用 **NoteBridge** 名称，GitHub 仓库仍为
[CalWade/wx-watcher](https://github.com/CalWade/wx-watcher)，保留微信、本地数据库和监听相关说明，便于发现项目。
更名是产品标识调整，不能据此保证规避平台检测或降低封号风险。

## 新入口与标识

| 项目 | 0.3 入口 |
| --- | --- |
| 主应用 / 主进程 | `/Applications/NoteBridge.app` / `NoteBridge` |
| 独立凭据应用 / 主进程 | `/Applications/NoteBridge Setup.app` / `NoteBridgeSetup` |
| 后台 / 凭据访问程序 | `notebridge` / `notebridge-access` |
| 应用标识 | `io.github.calwade.notebridge` / `io.github.calwade.notebridge.setup` |
| 命令行 | `uv run notebridge desktop`，或 `python -m note_bridge desktop` |
| 默认私有目录 | `~/Library/Application Support/NoteBridge` |
| DSH 桥接标识及存储 | `notebridge` / `~/.dsh/storages/notebridge` |

原 `wx-watcher` 命令、`wx_watcher` Python 接口和旧环境变量保留兼容。
新安装包使用新的程序名称；旧标识仍可能出现在兼容代码、历史任务或旧钥匙串项目中。
项目不承诺移除所有历史字符串，也不将更名描述为“不可检测”。

## 升级操作

1. 安装新版本，打开 **NoteBridge**。无需先删除旧数据，也无需重新抓取密钥。
2. 应用会请求旧版正常退出，等待其数据库和后台任务释放，再迁移本机资料。
3. macOS 将新应用标识视为不同身份。按系统要求给 **NoteBridge** 开启完整磁盘访问权限；
   未自动列出时，把窗口底部的应用图标拖入权限列表，再点「重新连接」。
4. 首次读取旧钥匙串项目时，macOS 可能要求确认新凭据程序的访问。密码只交给系统窗口。
   成功读取后，工具按原凭据引用保存到新的服务命名空间，保留旧项供兼容和回退。
5. 若升级前正在监听，安装迁移会记录恢复请求；权限与桥接就绪后恢复同一账号、同一会话。
   手动暂停的监听不会被默认启用。可在运行概览核对状态。

更名和资料迁移不需要临时修改微信签名、附加调试器或退出微信账号。
只有用户另外启动首次取钥向导时才进入该流程。

## 自动迁移的内容

- 在旧版退出后，将 `WXWatcher` 资料目录原子更名为 `NoteBridge`，保留旧路径的兼容链接。
- 保持账号 ID、凭据引用、消息数据库、事件游标、任务 ID 和原始聊天正文不变。
  配置修改前留存权限为 600 的备份；仅重写已知路径字段，不替换用户正文中的旧名称。
- 将旧 `keytool/jobs` 迁移到 `setup/jobs`，保留旧入口；已恢复客户端的任务和官方备份继续保留。
- 在旧 DSH 桥接无活动任务、监听端口关闭后，迁移其 token、工作目录和持久 jobs，保留旧路径链接。
  更新自己拥有的桥接条目，不改变模型渠道、其他插件或 cost-meter 账本。
- 迁移已开启的本项目登录启动项到新应用标识；原来未开启的保持关闭。
- 读取兼容旧钥匙串服务和旧 `wx-keytool-backup-v1` 加密备份。
  新导出使用 `notebridge-backup-v1`；口令与密钥不会写入公开仓库或日志。

## 中断、冲突与回退

迁移留有事务记录，中断后再次启动可继续完成。新旧实际目录同时存在时停止，
不自动覆盖或混合两份数据。旧目录是自定义链接时，请先通过 `NOTE_BRIDGE_HOME`
显式指定要使用的数据目录。

若仍有未完成的微信客户端恢复任务，先完成恢复再升级。不得删除其中的官方备份。

需要回退时先暂停并退出 NoteBridge，保留新旧路径和备份。旧钥匙串项目与原始任务仍保留；
恢复旧应用前应检查登录启动项和桥接状态。不要用一份历史 DSH 配置覆盖后来新增的模型或插件。
旧版不认识新备份格式；为跨版本回退保留原加密备份。
