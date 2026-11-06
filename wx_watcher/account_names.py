"""Account display metadata; never use names to select credentials or databases."""

import re
import unicodedata
from pathlib import Path

from .domain.account_names import clean_label as clean_label
from .domain.account_names import display_name as display_name


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
