"""受控 shell 执行（Day5：bash；Day10：加沙箱与权限）。"""
from __future__ import annotations
from .base import Tool
import subprocess

def _bash(command: str, timeout: int = 30) -> str:
    # TODO[Day5] subprocess 执行，捕获 stdout/stderr/returncode，超时保护
    # TODO[Day10] 接入权限层 + 沙箱（bwrap/firejail/docker），危险命令需确认
    #raise NotImplementedError("Day5：实现 bash")
    try:
        p = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout}s 未结束：{command}"
    out = p.stdout or ""
    if p.stderr:
        out += f"\n[stderr]\n{p.stderr}"
    if p.returncode != 0:
        out += f"\n[returncode={p.returncode}]"
    return out.strip() or "[无输出]"

bash_tool = Tool(
    name="bash",
    description="在工作目录中执行一条 shell 命令并返回输出。",
    parameters={"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
    run=_bash,
)
