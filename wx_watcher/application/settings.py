"""Application commands for Settings; concrete adapters are supplied by composition."""

import re
from pathlib import Path

from ..domain.settings import validate_settings
from .ports import SettingsContext


class SettingsUseCases:
    operations = ("settings", "run-settings", "feishu-secret", "login-service")

    def __init__(self, context: SettingsContext):
        self.context = context

    def handle(self, operation, data):
        if operation == "run-settings":
            if "login_enabled" in data and type(data["login_enabled"]) is not bool:
                raise ValueError("登录启动开关无效")
            self.context.perform(
                "settings",
                {
                    k: data[k]
                    for k in ["send_enabled", "autostart_monitor"]
                    if k in data
                },
            )
            if "login_enabled" in data:
                installed = self.context.login_installed()
                if installed != data["login_enabled"]:
                    self.context.perform(
                        "login-service",
                        {"action": "install" if data["login_enabled"] else "uninstall"},
                    )
            return {"saved": True}
        if operation == "settings":
            if self.context.services.running():
                raise ValueError("请先暂停监听，再修改配置")
            previous = self.context.state.load()
            values, switching = validate_settings(
                data, previous["profiles"], previous["active_profile"]
            )
            if {"agent_provider", "agent_options"}.intersection(values):
                provider = values.get("agent_provider", previous["agent_provider"])
                options = values.get("agent_options", previous["agent_options"])
                self.context.validate_agent(provider, options)
                if (provider, options) != (
                    previous["agent_provider"],
                    previous["agent_options"],
                ) and self.context.tasks.has_inflight():
                    raise ValueError("请先处理已有 Agent 任务后再更改内核或连接配置")
            if "knowledge_files" in values:
                self.context.select_passages(
                    tuple(Path(p) for p in values["knowledge_files"]), "验证资料"
                )
            if "send_enabled" in values and type(values["send_enabled"]) is not bool:
                raise ValueError("发送开关无效")
            if "active_profile" in values:
                profile = next(
                    p
                    for p in self.context.state.load()["profiles"]
                    if p["id"] == values["active_profile"]
                )
                self.context.require_current_account(profile)
                verified = self.context.credential_request(
                    {
                        "operation": "verify",
                        "db_root": profile["db_root"],
                        "key_ref": profile["key_ref"],
                    }
                )
                if not verified.get("all_verified"):
                    raise ValueError(
                        "此账号已保存的密钥未通过数据库验证，请先在微信凭据中修复连接。原配置未更改。"
                    )
                if switching:
                    values.update(
                        chat_id="",
                        group_name="",
                        send_enabled=False,
                        autostart_monitor=False,
                    )
            if not switching and {"chat_id", "group_name"}.intersection(values):
                self.context.require_local_account()
            previous = self.context.state.load()
            if any(
                k in values and values[k] != previous[k]
                for k in ["feishu_transport", "feishu_app_id"]
            ):
                values.update(
                    feishu_chat_id="", feishu_chat_name="", send_enabled=False
                )
            self.context.state.update(**values)
            if "active_profile" in values:
                self.context.refresh_account_names(profile["db_root"], quiet=True)
            if switching:
                self.context.close_preview()
                return {
                    "saved": True,
                    "message": "已验证并切换账号。请重新选择微信会话，确认飞书接收群后再开启投递与监控。",
                }
            return {"saved": True}
        if operation == "feishu-secret":
            if self.context.services.running():
                raise ValueError("请先暂停监听再修改飞书凭据")
            if not re.fullmatch(r"cli_[A-Za-z0-9]+", str(data.get("app_id", ""))):
                raise ValueError("飞书 App ID 无效")
            answer = self.context.credential_request(
                {"operation": "store_secret", "secret": data["secret"]}
            )
            previous = self.context.state.load()
            destination = {}
            if (
                previous["feishu_app_id"] != data["app_id"]
                or previous["feishu_transport"] != "openapi"
            ):
                destination = {
                    "feishu_chat_id": "",
                    "feishu_chat_name": "",
                    "send_enabled": False,
                }
            self.context.state.update(
                **destination,
                feishu_app_id=data["app_id"],
                feishu_secret_ref=answer["key_ref"],
                feishu_transport="openapi",
            )
            return {"saved": True}
        if operation == "login-service":
            return self.context.manage_login(str(data.get("action", "status")))
        raise ValueError("未知操作")
