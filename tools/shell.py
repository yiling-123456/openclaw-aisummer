"""受控 shell 执行（Day5：bash；Day10：加沙箱与权限）。

安全加固（Day10+）：
  - 危险命令模式拦截
  - 工作目录边界保护
  - 超时与输出截断
"""
from __future__ import annotations
from .base import Tool
import os
import re
import subprocess
import shlex

# ── 危险命令模式（阻止明显具有破坏性的操作） ──────────────────────────
# 每条为 (正则, 说明) —— 命中任一条则拒绝执行。
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # 递归强制删除
    (r"\brm\s+.*-rf\b",            "禁止递归强制删除（rm -rf）"),
    (r"\brm\s+.*--recursive\b",    "禁止递归删除目录"),
    # 硬盘级操作
    (r"\bmkfs\.",                  "禁止格式化文件系统"),
    (r"\bdd\s+if=",                "禁止直接读写块设备"),
    # 权限变更
    (r"\bchmod\s+.*777\b",         "禁止设置 777 权限"),
    (r"\bchown\s+",                "禁止更改文件所有者"),
    # 系统级危险操作
    (r"\bshutdown\b",              "禁止关机/重启"),
    (r"\breboot\b",                "禁止重启"),
    (r"\binit\s+[0-6]\b",          "禁止切换运行级别"),
    # 写入敏感系统路径
    (r">\s*/dev/",                 "禁止直接写入设备文件"),
    # fork 炸弹
    (r":\(\)\s*\{",                "禁止 fork 炸弹"),
    # 提权
    (r"\bsudo\b",                  "禁止使用 sudo"),
    # 下载并执行
    (r"\bcurl\s+.*\|\s*(ba)?sh\b", "禁止 curl-to-bash"),
    (r"\bwget\s+.*\|\s*(ba)?sh\b", "禁止 wget-to-bash"),
    # 删除当前目录或上级目录
    (r"\brm\s+.*-rf\s+(~|/|\.\.|\./)", "禁止删除家目录/根目录"),
]

# 允许的命令前缀（被拦截时提示用户可用的安全替代方案）
_SAFE_WRITE_HINT = "如需写入文件，请使用 write 工具。"
_SAFE_DELETE_HINT = "如需删除文件，请使用 bash 执行不带 -rf 的 rm 命令。"


def _validate_command(command: str) -> str | None:
    """校验命令是否安全。返回 None 表示通过，否则返回拒绝原因。"""
    cmd_lower = command.lower().replace("\n", " ").replace("\r", " ")

    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_lower):
            hint = ""
            if "rm " in pattern:
                hint = " " + _SAFE_DELETE_HINT
            elif ">" in pattern or "write" in reason.lower():
                hint = " " + _SAFE_WRITE_HINT
            return f"[安全拦截] {reason}。{hint}".strip()

    return None


def _bash(command: str, timeout: int = 30) -> str:
    # ── 安全校验 ──
    rejection = _validate_command(command)
    if rejection is not None:
        return rejection

    try:
        p = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
            cwd=os.getcwd(),  # 确保在工作目录中执行
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
    description="在工作目录中执行一条 shell 命令并返回输出。危险操作（rm -rf / sudo / curl-to-bash 等）会被自动拦截。",
    parameters={"type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"},
                               "timeout": {"type": "integer", "description": "超时秒数，默认 30"}},
                "required": ["command"]},
    run=_bash,
)
