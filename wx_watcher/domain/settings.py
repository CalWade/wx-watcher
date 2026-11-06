"""Validate configuration changes before any I/O or mutation occurs."""

import json
import re
from copy import deepcopy


def validate_settings(data, profiles, active_profile):
    allowed = {
        "active_profile",
        "chat_id",
        "group_name",
        "business_context",
        "knowledge_files",
        "reply_rules",
        "feishu_chat_id",
        "feishu_chat_name",
        "feishu_transport",
        "feishu_app_id",
        "agent_provider",
        "agent_options",
        "reader_port",
        "send_enabled",
        "autostart_monitor",
        "debounce_seconds",
        "max_daily_calls",
        "retention_days",
    }
    values = {k: deepcopy(v) for k, v in data.items() if k in allowed}
    for k in [
        "business_context",
        "reply_rules",
        "group_name",
        "chat_id",
        "feishu_chat_id",
        "feishu_app_id",
    ]:
        if k in values and (not isinstance(values[k], str) or len(values[k]) > 20000):
            raise ValueError("配置字段无效：" + k)
    for k in ["reader_port"]:
        if k in values and (
            type(values[k]) is not int or not 1024 <= values[k] <= 65535
        ):
            raise ValueError("端口无效")
    if "active_profile" in values and values["active_profile"] not in {
        p["id"] for p in profiles
    }:
        raise ValueError("账号配置不存在")
    switching = (
        "active_profile" in values and values["active_profile"] != active_profile
    )
    for k in ["max_daily_calls", "retention_days"]:
        if k in values and (type(values[k]) is not int or not 0 <= values[k] <= 10000):
            raise ValueError("数量设置无效")
    if "debounce_seconds" in values and (
        type(values["debounce_seconds"]) not in {float, int}
        or not 0 <= values["debounce_seconds"] <= 30
    ):
        raise ValueError("消息合并等待时间无效")
    if "chat_id" in values and not re.fullmatch(
        r"[A-Za-z0-9_.@-]{1,256}", values["chat_id"]
    ):
        raise ValueError("会话 ID 无效")
    for key in ("send_enabled", "autostart_monitor"):
        if key in values and type(values[key]) is not bool:
            raise ValueError("发送或启动开关无效")
    if "feishu_transport" in values and values["feishu_transport"] not in {
        "cli",
        "openapi",
    }:
        raise ValueError("飞书连接方式无效")
    if "agent_provider" in values and (
        not isinstance(values["agent_provider"], str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", values["agent_provider"])
    ):
        raise ValueError("Agent 标识无效")
    if "agent_options" in values and (
        not isinstance(values["agent_options"], dict)
        or len(json.dumps(values["agent_options"])) > 10000
    ):
        raise ValueError("Agent 配置无效")
    return values, switching
