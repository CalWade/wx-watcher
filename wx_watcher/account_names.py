"""Account display metadata; never use names to select credentials or databases."""

import re
import unicodedata
from pathlib import Path


def clean_label(value):
    if not isinstance(value, str):
        raise ValueError("账号备注必须是文字")
    value = value.strip()
    if len(value) > 80 or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise ValueError("账号备注最多 80 个字，不能包含换行或控制字符")
    return value


def display_name(root, names=None, profile=None):
    account_id = Path(root).parent.name
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


def read_identity(reader, root):
    """Read only the exact self contact, never infer ownership from contact order."""
    account = Path(root).parent.name
    if not re.fullmatch(r"wxid_[A-Za-z0-9_]+", account):
        return {}
    candidates = {account}
    match = re.fullmatch(r"(wxid_[A-Za-z0-9_]+)_[0-9a-fA-F]{4}", account)
    if match:
        candidates.add(match[1])
    db = Path(root) / "contact/contact.db"
    columns = {r["name"] for r in reader.rows(db, "PRAGMA table_info(contact);")}
    if "username" not in columns:
        return {}
    wanted = [c for c in ("username", "nick_name", "alias") if c in columns]
    quoted = ",".join("'" + c.replace("'", "''") + "'" for c in sorted(candidates))
    rows = reader.rows(
        db,
        "SELECT "
        + ",".join(wanted)
        + " FROM contact WHERE username IN ("
        + quoted
        + ") LIMIT 2;",
    )
    if len(rows) != 1 or rows[0].get("username") not in candidates:
        return {}

    def name(field):
        value = rows[0].get(field)
        if not isinstance(value, str) or len(value) > 256:
            return ""
        return (
            ""
            if any(unicodedata.category(c) in {"Cc", "Cs"} for c in value)
            else value.strip()
        )

    return {"nickname": name("nick_name"), "wechat_id": name("alias")}
