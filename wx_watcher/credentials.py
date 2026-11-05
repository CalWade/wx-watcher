"""Native Keychain bridge. Secret bytes only cross private stdin/stdout pipes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def native_tool(explicit: str = "") -> Path:
    candidates = [
        explicit,
        os.environ.get("NOTE_BRIDGE_ACCESS_BIN", ""),
        os.environ.get("WX_KEYTOOL_BIN", ""),
        shutil.which("notebridge-access") or "",
        shutil.which("wx-keytool") or "",
    ]
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        candidates.append(str(Path(sys.executable).parent.parent / "notebridge-access"))
    candidates.append(
        str(
            Path(__file__).resolve().parents[1]
            / "native/.build/release/notebridge-access"
        )
    )
    for value in candidates:
        if value and Path(value).is_file() and os.access(value, os.X_OK):
            return Path(value)
    raise FileNotFoundError("未找到 notebridge-access；请安装 Mac 应用或构建 native 包")


def native_request(data: dict, executable: str = "", *, internal_read=False) -> dict:
    args = [str(native_tool(executable))]
    if internal_read:
        args.append("--internal-read")
    result = subprocess.run(
        args,
        input=json.dumps(data),
        text=True,
        capture_output=True,
        timeout=120,
        cwd="/private/tmp",
    )
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("原生凭据工具返回无效结果") from None
    if result.returncode != 0 or not answer.get("ok"):
        if answer.get("error") == "database_access_denied":
            raise PermissionError(
                "macOS 尚未允许本应用读取微信数据。请在系统设置 → 隐私与安全性 → 完整磁盘访问权限中允许 "
                + os.environ.get("NOTE_BRIDGE_APP_NAME", "NoteBridge")
                + "，然后退出并重新打开本应用。已有密钥无需重新获取。"
            )
        raise RuntimeError(
            "凭据操作失败：" + str(answer.get("error", "native_tool_failed"))
        )
    return answer


def load_reference(reference: str, executable: str = "") -> bytes:
    answer = native_request(
        {"operation": "read", "key_ref": reference}, executable, internal_read=True
    )
    try:
        value = bytes.fromhex(answer.pop("key_hex"))
        if len(value) != 32:
            raise ValueError
        return value
    except (ValueError, KeyError):
        raise RuntimeError("凭据工具未返回有效的数据库密钥") from None
