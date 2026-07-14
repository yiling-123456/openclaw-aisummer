"""Todo 规划工具（Day9+: Planning Layer）。

将 PlanningManager 暴露为 Tool，让模型能够：
  - todo_write：分解任务时列出待办项
  - todo_update：推进 / 标记待办状态

与 Claude Code 的 TodoWrite / TodoUpdate 设计一致。
"""
from __future__ import annotations
import json

from tools.base import Tool
from agent.planning import get_planner, TodoStatus


# ── 工具实现 ─────────────────────────────────────────────────────────

def _todo_write(title: str = "", note: str = "", items: str = "") -> str:
    """添加待办项。支持单条或批量。

    Args:
        title: 单条待办的标题（与 items 二选一）。
        note: 单条待办的备注说明。
        items: 批量添加的 JSON 数组字符串。
              每个元素格式：{"title": str, "note": str (可选)}

    Returns:
        格式化结果文本。
    """
    planner = get_planner()

    # ── 批量添加（items JSON） ──
    if items and items.strip():
        try:
            item_list = json.loads(items)
        except json.JSONDecodeError:
            return (
                f"[todo_write] items 参数解析失败，不是合法的 JSON 数组。\n"
                f"请使用 JSON 数组格式："
                f'[{{"title": "第一步", "note": "说明"}}, {{"title": "第二步"}}]'
            )
        if not isinstance(item_list, list):
            return "[todo_write] items 参数必须是 JSON 数组（以 [ 开头）。"

        added = []
        for i, item in enumerate(item_list):
            if not isinstance(item, dict) or "title" not in item:
                return f"[todo_write] items[{i}] 缺少 title 字段。每条必须有 title。"
            t = item["title"]
            n = item.get("note", "")
            todo = planner.add(t, n)
            note_suffix = f" — {n}" if n else ""
            added.append(f"  • `[#{todo.id}]` {t}{note_suffix}")

        return f"[todo_write] 已添加 {len(added)} 项待办：\n" + "\n".join(added)

    # ── 单条添加 ──
    if not title:
        return (
            "[todo_write] 请提供 title（单条）或 items（批量）。\n"
            "示例：todo_write(title='搜索课程教师', note='使用 course_search')\n"
            "      或 todo_write(items='[{\"title\": \"步骤1\"}]')"
        )

    todo = planner.add(title, note)
    note_str = f" — {note}" if note else ""
    return f"[todo_write] 已添加：`[#{todo.id}]` {title}{note_str}"


def _todo_update(todo_id: int, status: str, result: str = "") -> str:
    """更新待办项状态。

    Args:
        todo_id: 待办项 ID（来自 todo_write 输出的 [#ID]）。
        status: 目标状态：pending / in_progress / completed / blocked / cancelled。
        result: 可选的执行结果描述。

    Returns:
        格式化结果文本。
    """
    planner = get_planner()

    # ── 验证 status ──
    valid_statuses = {s.value for s in TodoStatus}
    if status not in valid_statuses:
        return (
            f"[todo_update] 无效状态：'{status}'。必须是以下之一：\n"
            + "\n".join(f"  • {s}：{_status_desc(s)}" for s in sorted(valid_statuses))
        )

    # ── 如果标记 blocked，检查是否需要先记录反思 ──
    item = planner.get(todo_id)
    if item is None:
        return (
            f"[todo_update] 未找到 ID 为 {todo_id} 的待办项。"
            f"请先用 todo_write 添加，或用 task_list(action='list') 查看现有待办。"
        )

    current_status = item.status.value
    updated = planner.update(todo_id, status, result)
    if updated is None:
        return (
            f"[todo_update] 无法将 `[#{todo_id}]` {item.title} 更新为「{status}」。\n"
            f"当前状态是「{current_status}」，不允许此转换（请先设为 in_progress）。"
        )

    status_icon = {
        "pending": "📋",
        "in_progress": "🔄",
        "completed": "✅",
        "blocked": "🚧",
        "cancelled": "❌",
    }.get(status, "•")

    result_str = f" — {result}" if result else ""
    return f"[todo_update] {status_icon} `[#{updated.id}]` {updated.title} → **{status}**{result_str}"


def _status_desc(s: str) -> str:
    return {
        "pending": "待处理 / 解封受阻项",
        "in_progress": "正在推进",
        "completed": "已完成",
        "blocked": "受阻，无法继续",
        "cancelled": "取消，不再需要",
    }.get(s, "")


# ── Tool 定义 ────────────────────────────────────────────────────────

todo_write_tool = Tool(
    name="todo_write",
    description=(
        "创建待办事项，把长任务拆成可跟踪的子步骤。\n"
        "面对**多步骤长任务**时，第一步先用 todo_write 列出所有步骤，"
        "然后逐条推进、逐条标记完成。\n"
        "• 单条添加：todo_write(title='步骤名称', note='说明')\n"
        "• 批量添加：todo_write(items='[{\"title\": \"步骤1\", \"note\": \"说明\"}]')\n"
        "典型用法：分析4门课程 → 先 add 4个待办，每完成一门 update 为 completed。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "待办项标题（与 items 二选一）。",
            },
            "note": {
                "type": "string",
                "description": "可选的备注，如预期方法、注意事项。",
            },
            "items": {
                "type": "string",
                "description": "批量添加的 JSON 数组字符串，如 '[{\"title\": \"步骤1\"}]'。与 title 二选一。",
            },
        },
    },
    run=_todo_write,
)

todo_update_tool = Tool(
    name="todo_update",
    description=(
        "更新待办事项的状态。完成或推进一个子步骤后调用。\n"
        "status 可选值：\n"
        "  • pending — 待处理（也用于解封受阻项）\n"
        "  • in_progress — 正在做（开始一项工作前标记）\n"
        "  • completed — 已完成（完成后标记，建议填写 result 总结）\n"
        "  • blocked — 受阻，暂时无法继续\n"
        "  • cancelled — 已取消，不再需要\n"
        "示例：todo_update(todo_id=1, status='completed', result='已获取张老师评价')"
    ),
    parameters={
        "type": "object",
        "properties": {
            "todo_id": {
                "type": "integer",
                "description": "待办项 ID（添加时返回的 [#ID] 数字）。",
            },
            "status": {
                "type": "string",
                "description": "新状态：pending / in_progress / completed / blocked / cancelled",
            },
            "result": {
                "type": "string",
                "description": "可选的执行结果总结（完成时填写）。",
            },
        },
        "required": ["todo_id", "status"],
    },
    run=_todo_update,
)
