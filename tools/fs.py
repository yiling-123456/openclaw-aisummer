"""文件读写工具（Day5：read / write）。"""
from __future__ import annotations
from .base import Tool


def _read(path: str, max_bytes: int = 100_000) -> str:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read(max_bytes + 1)
    truncated = len(text) > max_bytes
    if truncated:
        text = text[:max_bytes]
    lines = text.splitlines()
    body = "\n".join(f"{i:>6}\t{ln}" for i, ln in enumerate(lines, 1))
    if truncated:
        body += f"\n... [已截断，仅显示前 {max_bytes} 字节]"
    return body or "[空文件]"


def _write(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        n = f.write(content)
    return f"已写入 {n} 字节到 {path}"


read_tool = Tool(
    name="read",
    description="读取指定路径的文本文件内容。",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"]},
    run=_read,
)

write_tool = Tool(
    name="write",
    description="把内容写入指定路径（覆盖）。",
    parameters={"type": "object",
                "properties": {"path": {"type": "string"},
                               "content": {"type": "string"}},
                "required": ["path", "content"]},
    run=_write,
)
