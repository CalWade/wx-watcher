# Agent 接入与更换

默认使用本机 DSH，复用其模型渠道与 dsh-cost-meter。Agent 能力通过独立适配器接入，回复任务的采集、解析、预算和投递流程通过统一接口调用。

## Python 接口

接口定义在 `wx_watcher/application/ports.py`，返回值定义在 `wx_watcher/domain/agents.py`。

| 成员 | 约定 |
| --- | --- |
| `provider_id` | 稳定的提供方标识 |
| `prepare()` | 准备连接，返回 `ready` 和可读的连接结果 |
| `status()` | 返回提供方状态和能力 |
| `submit(id, prompt)` | 幂等提交，返回 `AgentJob` |
| `poll(id)` | 查询同一个持久任务，返回 `AgentJob` |

`AgentJob` 使用 `queued`、`running`、`completed`、`failed`、`interrupted` 状态。`id` 和 `provider_id` 必须对应请求。结果包括字符串 `output`、字典 `usage` 与 `cost`、字符串 `session_ref`、有界错误信息 `error` 和 `tool_count`。

本项目的回复生成要求 `tool_count` 明确为 0。适配器应报告请求可用的工具数，完成后由应用层检查隔离条件并解析回复 JSON。模型返回格式由 `domain/replies.py` 定义。

`cost` 可包含 `recorded`、`amount`、`currency`。`status().capabilities.cost_tracking` 为真时，接入测试要求费用记录成功。每个任务的原始用量与费用摘要随任务保存。

## 通用本机 HTTP 协议

`HttpAgentGateway` 调用以下接口，使用仅本人可读写的 token 文件生成 Bearer 认证头。

| 请求 | 结果 |
| --- | --- |
| `POST /v1/prepare`，请求体 `{}` | 准备结果，包含 `ready` |
| `GET /v1/status` | `provider_id`、`ready`、`capabilities` |
| `POST /v1/jobs`，请求体包含 `id`、`prompt` | 标准 `AgentJob` JSON |
| `GET /v1/jobs/{id}` | 标准 `AgentJob` JSON |

该适配器的 `provider_id` 固定为 `http`。服务仅绑定 `127.0.0.1`。配置通过设置接口传入，token 内容保留在本机私有文件中。

```json
{
  "agent_provider": "http",
  "agent_options": {
    "port": 18766,
    "token_file": "/example/private/agent-token"
  }
}
```

服务应持久化任务，同一 ID 与相同 prompt 重复提交应返回同一执行记录。相同 ID 配上不同 prompt 应拒绝。`poll` 必须在宿主重启后仍能返回原任务的结果或明确的失败状态。

## 新增内嵌实现

1. 在 `infrastructure/agents/` 实现 `AgentGateway`。引擎初始化、模型渠道、会话存储与依赖由适配器管理。
2. 在 `composition.agent_registry()` 中注册稳定标识、工厂和配置校验器。该入口同时供桌面与后台工作进程使用。
3. 若引擎需要独立进程，把启动与资源释放放在其宿主中，通过统一 HTTP 协议连接。
4. 使用 `ReplyWorker` 的内存测试模式验证格式、工具隔离、失败、重启与重复提交，并增加实际适配器的契约测试。
5. 更新 `tests/features.json` 与相关接入文档，运行完整检查。

当前交付包含接口与注册机制、DSH 实现、通用 HTTP 实现及用于单元测试的内存实现。具体内嵌推理引擎需要后续选择并开发适配器。

## 旧数据与切换

旧版本缺少提供方字段的任务继续绑定 DSH。旧的 `dsh` 结果在读取时转换为标准 `agent` 结果，保留原任务和会话身份。旧设置中的 `dsh_port` 通过兼容转换进入标准配置。

暂停监控并处理完未完成任务后再切换 Agent。手工修改配置的开发者同样需要遵守任务绑定与宿主持久化约定，避免把旧任务交给另一个后端。
