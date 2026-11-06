"""Account labels are display values and never credential identities."""

import unicodedata
from pathlib import PurePath


def clean_label(value):
    if not isinstance(value, str):
        raise ValueError("账号备注必须是文字")
    value = value.strip()
    if len(value) > 80 or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise ValueError("账号备注最多 80 个字，不能包含换行或控制字符")
    return value


def display_name(root, names=None, profile=None):
    account_id = PurePath(root).parent.name
    names = names or {}
    fallback = "未命名账号 · " + account_id[-4:]
    legacy = (profile or {}).get("label", "")
    custom = names.get(
        "custom_label", legacy if legacy not in {account_id, fallback} else ""
    )
    nickname, wechat_id = names.get("nickname", ""), names.get("wechat_id", "")
    automatic = (
        f"{nickname} · {wechat_id}"
        if nickname and wechat_id and nickname != wechat_id
        else nickname or wechat_id
    )
    return {
        "label": custom or automatic or fallback,
        "name_known": bool(custom or automatic),
        "custom_label": custom,
        "nickname": nickname,
        "wechat_id": wechat_id,
        "account_id": account_id,
    }
