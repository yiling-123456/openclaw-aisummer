"""权限分级系统（Day10 安全层）。

实现按操作破坏性分级的权限控制：
  - READ_ONLY：只读操作，自动放行
  - WRITE：写操作，限工作目录内（由 fs.py 的 _resolve_safe 保证）
  - EXECUTE：shell 执行，危险命令由 shell.py 的 _DANGEROUS_PATTERNS 拦截
  - NETWORK：外传操作，SSRF 由 more_tools.py 的 _validate_url 防护

用法：
  from agent.permission import PermissionChecker, PermissionTier, TOOL_TIER_MAP
  checker = PermissionChecker()
  tier = TOOL_TIER_MAP.get(tool_name, PermissionTier.READ_ONLY)
  checker.check(tool_name, arguments)
  stats = checker.get_stats()
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Callable


class PermissionTier(Enum):
    """操作破坏性分级。"""
    READ_ONLY = "read_only"    # 只读放行
    WRITE = "write"            # 写操作（限工作目录）
    EXECUTE = "execute"        # shell 执行
    NETWORK = "network"        # 外传（web_fetch）


# ── 工具 → 权限等级映射 ──────────────────────────────────────────
TOOL_TIER_MAP: dict[str, PermissionTier] = {
    # 只读
    "read": PermissionTier.READ_ONLY,
    "grep": PermissionTier.READ_ONLY,
    "glob": PermissionTier.READ_ONLY,
    "teacher_search": PermissionTier.READ_ONLY,
    "course_search": PermissionTier.READ_ONLY,
    "recall_memory": PermissionTier.READ_ONLY,
    "list_memories": PermissionTier.READ_ONLY,
    "task_list": PermissionTier.READ_ONLY,
    "todo_write": PermissionTier.READ_ONLY,
    "todo_update": PermissionTier.READ_ONLY,
    # 写
    "write": PermissionTier.WRITE,
    "edit": PermissionTier.WRITE,
    "save_memory": PermissionTier.WRITE,
    "forget_memory": PermissionTier.WRITE,
    # 执行
    "bash": PermissionTier.EXECUTE,
    # 网络
    "web_fetch": PermissionTier.NETWORK,
    # ── MCP server 工具（filesystem server）──
    # 只读 → 自动放行
    "mcp__echo": PermissionTier.READ_ONLY,
    "mcp__list_allowed_directories": PermissionTier.READ_ONLY,
    "mcp__list_directory": PermissionTier.READ_ONLY,
    "mcp__list_directory_with_sizes": PermissionTier.READ_ONLY,
    "mcp__directory_tree": PermissionTier.READ_ONLY,
    "mcp__get_file_info": PermissionTier.READ_ONLY,
    "mcp__read_file": PermissionTier.READ_ONLY,
    "mcp__read_media_file": PermissionTier.READ_ONLY,
    "mcp__read_multiple_files": PermissionTier.READ_ONLY,
    "mcp__read_text_file": PermissionTier.READ_ONLY,
    "mcp__search_files": PermissionTier.READ_ONLY,
}

# MCP 工具前缀 — 不在 TOOL_TIER_MAP 中的 MCP 工具默认视为 EXECUTE 级别
_MCP_PREFIX = "mcp__"

# 各等级的简短中文说明
TIER_LABELS: dict[PermissionTier, str] = {
    PermissionTier.READ_ONLY: "只读",
    PermissionTier.WRITE: "写入",
    PermissionTier.EXECUTE: "执行",
    PermissionTier.NETWORK: "网络",
}


def get_tier(tool_name: str) -> PermissionTier:
    """获取工具对应的权限等级。

    未知工具（含 MCP 工具）默认归为 EXECUTE 级别，
    因为来自外部 server 的工具能力不可预知。
    """
    if tool_name in TOOL_TIER_MAP:
        return TOOL_TIER_MAP[tool_name]
    if tool_name.startswith(_MCP_PREFIX):
        return PermissionTier.EXECUTE
    # 未知内置工具，保守按只读处理（实际会在 loop.py 中被拦截为未知工具）
    return PermissionTier.READ_ONLY


class PermissionChecker:
    """权限检查器。

    在每个工具调用前检查权限等级，记录统计信息。
    支持回调机制，供交互模式在 WRITE/EXECUTE/NETWORK 级别时询问用户确认。
    """

    def __init__(self):
        # 各等级调用计数
        self._stats: dict[PermissionTier, int] = {
            PermissionTier.READ_ONLY: 0,
            PermissionTier.WRITE: 0,
            PermissionTier.EXECUTE: 0,
            PermissionTier.NETWORK: 0,
        }
        # 被拒绝的调用计数
        self._denied: int = 0
        # 高风险操作回调：返回 True 表示允许，False 表示拒绝
        # 签名为 (tool_name: str, tier: PermissionTier, arguments: dict) -> bool
        self._high_risk_callback: Callable[[str, PermissionTier, dict], bool] | None = None

    def set_high_risk_callback(
        self, cb: Callable[[str, PermissionTier, dict], bool] | None
    ) -> None:
        """设置高风险操作回调。

        当回调返回 False 时，工具调用会被拒绝。
        设为 None 则自动放行所有操作（默认行为）。
        """
        self._high_risk_callback = cb

    def check(self, tool_name: str, arguments: dict | None = None) -> tuple[bool, str]:
        """检查工具调用权限。

        Returns:
            (allowed, reason): allowed 为 False 时 reason 说明拒绝原因。
        """
        if arguments is None:
            arguments = {}

        tier = get_tier(tool_name)
        self._stats[tier] += 1

        # 只读操作：自动放行
        if tier == PermissionTier.READ_ONLY:
            return True, ""

        # WRITE/EXECUTE/NETWORK：如果有回调则询问，否则自动放行
        if self._high_risk_callback is not None:
            allowed = self._high_risk_callback(tool_name, tier, arguments)
            if not allowed:
                self._denied += 1
                label = TIER_LABELS.get(tier, "未知")
                return False, f"[权限拦截] {label}操作 '{tool_name}' 需要确认，已拒绝执行"

        return True, ""

    def get_stats(self) -> dict:
        """返回权限统计信息。"""
        return {
            "by_tier": {
                tier.value: count for tier, count in self._stats.items()
            },
            "total": sum(self._stats.values()),
            "denied": self._denied,
        }

    def format_stats(self) -> str:
        """格式化统计信息供日志/显示。"""
        s = self._stats
        total = sum(s.values())
        parts = []
        for tier in (PermissionTier.READ_ONLY, PermissionTier.WRITE,
                      PermissionTier.EXECUTE, PermissionTier.NETWORK):
            if s[tier] > 0:
                label = TIER_LABELS.get(tier, "未知")
                parts.append(f"{label}:{s[tier]}")
        result = f"[权限] 总调用 {total}（{', '.join(parts)}）"
        if self._denied > 0:
            result += f"，拒绝 {self._denied}"
        return result
