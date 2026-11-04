"""Small, local, deterministic knowledge selection; no external index service."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def select_passages(
    files: tuple[Path, ...], query: str, budget=6000
) -> tuple[str, list[dict]]:
    terms = set(re.findall(r"[a-zA-Z0-9]{2,}", query.lower()))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", query))
    terms.update(chinese[i : i + 2] for i in range(max(0, len(chinese) - 1)))
    passages, sources = [], []
    total = 0
    for path in files[:20]:
        if path.suffix.lower() not in {".md", ".txt"} or path.is_symlink():
            raise ValueError("业务资料只接受普通 Markdown/TXT 文件")
        raw = path.read_bytes()
        total += len(raw)
        if len(raw) > 1_000_000 or total > 4_000_000:
            raise ValueError("业务资料超过大小限制，请拆分或精简")
        text = raw.decode("utf-8")
        sources.append({"name": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for i, block in enumerate(re.split(r"\n\s*\n", text)):
            for offset in range(0, len(block), 1000):
                chunk = block[offset : offset + 1000].strip()
                if chunk:
                    score = sum(chunk.lower().count(t) for t in terms)
                    passages.append((score, path.name, i, offset, chunk))
    passages.sort(key=lambda p: (-p[0], p[1], p[2], p[3]))
    chosen, used = [], 0
    for _, name, index, _, chunk in passages:
        value = f"[资料：{name}，段落 {index + 1}]\n{chunk}"
        if used + len(value) > budget:
            continue
        chosen.append(value)
        used += len(value)
        if len(chosen) >= 8:
            break
    return "\n\n".join(chosen), sources
