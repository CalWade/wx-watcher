# 本地接口与项目复用

## 只读消息 API

读取服务绑定 `127.0.0.1`，要求 `Authorization: Bearer <token>`。
token 位于配置的 state_dir/api-token.txt，权限 600。不要把真实 token 放入公开命令、Issue 或日志。

| 请求 | 返回 |
| --- | --- |
| GET /v1/status | 数据源状态、scope_id、最后成功扫描、错误类别 |
| GET /v1/messages?limit=50 | 指定会话的已采集历史 |
| GET /v1/events?after=0&limit=200 | 持久化事件与 next_cursor |

没有任意 SQL、文件读取、进程控制或微信发送接口。其他 HTTP 方法返回 405。
事件游标已过保留期返回 410；消费者必须明确处理缺口，不能将其当成空批次。
GET /v1/status 中的 event_cursor 是当前持久化序号，即使过期事件已删除仍会保留高水位。
消息源未就绪返回 503，不把错误伪装成“没有新消息”。
消费者处理成功后保存 next_cursor；重试原游标可以重放，不会提前删除事件。
分片编号、账号和库重建代次参与唯一标识，不能仅用 local_id 当全局 ID。

源码调试：

```sh
uv run notebridge --config config/local.toml doctor
uv run notebridge --config config/local.toml serve
uv run python scripts/client.py --config config/local.toml status
```

配置示例见 `config/example.toml`。新应用的配置由向导写入私有目录。

## 凭据复用

新读取配置可用 `key_ref` 替代文件密钥，并通过 `NOTE_BRIDGE_ACCESS_BIN` 或 `keytool` 指定原生访问器。
主密钥只通过私有进程管道进入本机解密组件，不进入 HTTP。
默认建议复用只读消息 API；必须自行解密的可信本地组件才需要内部密钥读取能力。

## DSH bridge

Bearer 认证，默认 8766：

- GET /v1/status
- POST /v1/jobs，JSON `{id: <64-hex>, prompt: <bounded text>}`
- GET /v1/jobs/<id>

同 ID 相同 prompt 复用，冲突 409，队列满 429。
持久化状态包括 queued、running、completed、failed、interrupted。
正常响应不返回模型渠道凭据。运行中断时不自动再次生成。

## 管理台 API

管理台是本机应用内部接口，不是远程管理协议。
使用受保护的启动链接完成一次会话交换，随后使用 HttpOnly / SameSite cookie。
修改请求检查 Host、Origin、JSON 类型和自定义请求头；外部网页不能直接配置服务。
原生文件选择消息仅接受当前主框架的受信任本机来源。

账号名称的内部管理操作：`POST /api/action/account-name` 接收 `{db_root, label}`，只更新已发现或已保存账号的本地备注；空 `label` 清除备注。`POST /api/action/refresh-account-name` 接收 `{db_root}`，读取已保存凭据所对应的本人昵称与微信号。两者均按数据库目录定位，不用昵称或备注作为账号身份。显示元数据缓存于私有设置文件；账号列表只读缓存，不触发钥匙串读取。

`account-status` 将 `can_configure`（可继续配置所选本地账号）与 `matches_current`（已确认微信当前登录与所选账号一致）分开返回。已保存凭据且本地资料可用、没有已确认的其他登录账号时，可以搜索和选择会话；这不代表已登录，也不授权开始监控。`start` 和 `finish-setup` 仍检查当前登录账号；后台监控在登录身份丢失或变化时暂停。
