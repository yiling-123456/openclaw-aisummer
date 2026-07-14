"""规划层：TodoList 状态机 + 反思追踪 + 进度管理。

为 ReAct 主循环添加审议式规划能力（Day9+ 新增）：
- 任务分解为有序子任务，状态机管理生命周期
- 每步注入规划状态，模型始终知道"整体到哪了"
- 反思追踪（同一子任务最多反思 N 次，防无限套娃）
- 进度检测（无进展预警 + 完成判据）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── 状态机定义 ──────────────────────────────────────────────────────

class TodoStatus(str, Enum):
    """待办项的生命周期状态。

    合法转换：
      pending → in_progress → completed
                              → blocked → pending (解封重试)
                              → cancelled
    """
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


# 合法状态转换表
_VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.PENDING: {TodoStatus.IN_PROGRESS, TodoStatus.COMPLETED, TodoStatus.BLOCKED, TodoStatus.CANCELLED},
    TodoStatus.IN_PROGRESS: {TodoStatus.COMPLETED, TodoStatus.BLOCKED, TodoStatus.CANCELLED},
    TodoStatus.BLOCKED: {TodoStatus.PENDING, TodoStatus.CANCELLED},
    TodoStatus.COMPLETED: set(),
    TodoStatus.CANCELLED: set(),
}


class TransitionError(ValueError):
    """非法的状态转换。"""
    pass


# ── 核心数据类 ──────────────────────────────────────────────────────

@dataclass
class TodoItem:
    """一条待办项。"""
    id: int
    title: str
    note: str = ""
    status: TodoStatus = TodoStatus.PENDING
    reflection_count: int = 0      # 已反思次数
    error_count: int = 0           # 已错误次数
    result: str = ""               # 完成时的总结

    def transition_to(self, new_status: TodoStatus) -> None:
        """执行状态转换，非法时抛 TransitionError。"""
        allowed = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise TransitionError(
                f"不能从「{self.status.value}」转换到「{new_status.value}」"
                f"（允许的目标：{', '.join(s.value for s in allowed)}）"
            )
        self.status = new_status


# ── 规划管理器 ──────────────────────────────────────────────────────

class PlanningManager:
    """TodoList 状态机。

    管理任务的分解、跟踪、状态转换。
    提供格式化的规划状态用于注入上下文。
    """

    _todos: list[TodoItem]
    _counter: int
    max_reflections: int   # 同一子任务最多反思次数（防无限套娃）
    max_errors: int        # 同一子任务最多错误次数后自动标记 blocked

    def __init__(self, max_reflections: int = 3, max_errors: int = 5):
        self._todos = []
        self._counter = 0
        self.max_reflections = max_reflections
        self.max_errors = max_errors

    # ── 增删改查 ──

    def add(self, title: str, note: str = "") -> TodoItem:
        """添加一条待办项。"""
        self._counter += 1
        item = TodoItem(id=self._counter, title=title, note=note)
        self._todos.append(item)
        return item

    def update(self, todo_id: int, status: str, result: str = "") -> Optional[TodoItem]:
        """更新待办项状态。失败（非法转换 / ID 不存在）返回 None。"""
        try:
            new_status = TodoStatus(status)
        except ValueError:
            return None

        for item in self._todos:
            if item.id == todo_id:
                try:
                    item.transition_to(new_status)
                except TransitionError:
                    return None
                if result:
                    item.result = result
                return item
        return None

    def get(self, todo_id: int) -> Optional[TodoItem]:
        for item in self._todos:
            if item.id == todo_id:
                return item
        return None

    def get_current(self) -> Optional[TodoItem]:
        """获取当前正在进行的待办（in_progress 状态，优先最近更新的）。"""
        # 反向查找，取最后一个被标记为 in_progress 的
        current = None
        for item in self._todos:
            if item.status == TodoStatus.IN_PROGRESS:
                current = item
        return current

    def list(self) -> list[TodoItem]:
        return list(self._todos)

    def clear(self) -> int:
        """清除已完成/已取消的待办。返回清除数。"""
        before = len(self._todos)
        self._todos = [t for t in self._todos
                       if t.status not in (TodoStatus.COMPLETED, TodoStatus.CANCELLED)]
        return before - len(self._todos)

    # ── 状态查询 ──

    def all_done(self) -> bool:
        """所有待办是否都已完成或取消。"""
        if not self._todos:
            return False
        return all(
            item.status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED)
            for item in self._todos
        )

    def pending_or_in_progress(self) -> list[TodoItem]:
        return [t for t in self._todos
                if t.status in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS)]

    def completed_count(self) -> int:
        return sum(1 for t in self._todos if t.status == TodoStatus.COMPLETED)

    def progress_pct(self) -> float:
        """完成百分比（0-100）。"""
        if not self._todos:
            return 0.0
        return self.completed_count() / len(self._todos) * 100

    # ── 反思追踪 ──

    def record_reflection(self, todo_id: int) -> bool:
        """记录一次反思。返回 False 表示已超上限（该卡住了）。"""
        item = self.get(todo_id)
        if item is None:
            return False
        item.reflection_count += 1
        return item.reflection_count < self.max_reflections

    def increment_error(self, todo_id: int) -> None:
        """记录一次错误。"""
        item = self.get(todo_id)
        if item is not None:
            item.error_count += 1

    def is_stuck(self, todo_id: int) -> bool:
        """判断子任务是否卡死（反思超限或错误过多）。"""
        item = self.get(todo_id)
        if item is None:
            return False
        return (item.reflection_count >= self.max_reflections
                or item.error_count >= self.max_errors)

    # ── 状态摘要（注入上下文） ──

    def format_for_prompt(self) -> str:
        """格式化为注入上下文的规划状态文本。

        返回空字符串表示无需注入（没有待办）。
        """
        if not self._todos:
            return ""

        lines = ["📋 **当前规划**"]
        groups = {
            TodoStatus.IN_PROGRESS: "🔄 进行中",
            TodoStatus.PENDING:    "⏳ 待处理",
            TodoStatus.BLOCKED:    "🚧 受阻",
            TodoStatus.COMPLETED:  "✅ 已完成",
            TodoStatus.CANCELLED:  "❌ 已取消",
        }

        for status, label in groups.items():
            items = [t for t in self._todos if t.status == status]
            if not items:
                continue
            lines.append(f"\n**{label}**：")
            for t in items:
                tags = []
                if t.reflection_count > 0:
                    tags.append(f"🔁 反思{t.reflection_count}次")
                if t.error_count > 0:
                    tags.append(f"⚠️ 错误{t.error_count}次")
                tag_str = f" ({'; '.join(tags)})" if tags else ""
                note_str = f" — {t.note}" if t.note else ""
                result_str = f" ✅ {t.result}" if t.result and status == TodoStatus.COMPLETED else ""
                lines.append(f"  • `[#{t.id}]` **{t.title}**{note_str}{result_str}{tag_str}")

        pct = self.progress_pct()
        done = self.completed_count()
        total = len(self._todos)
        lines.append(f"\n📊 **进度**：{done}/{total}（{pct:.0f}%）")

        return "\n".join(lines)

    def format_short_summary(self) -> str:
        """简短的完成/未完成摘要（用于最终输出）。"""
        if not self._todos:
            return ""
        done = self.completed_count()
        total = len(self._todos)
        blocked = len([t for t in self._todos if t.status == TodoStatus.BLOCKED])
        cancelled = len([t for t in self._todos if t.status == TodoStatus.CANCELLED])
        parts = [f"已完成 {done}/{total}"]
        if blocked:
            parts.append(f"受阻 {blocked}")
        if cancelled:
            parts.append(f"取消 {cancelled}")
        return f"规划完成情况：{'，'.join(parts)}。"


# ── 模块级单例 ──────────────────────────────────────────────────────

_planner: PlanningManager | None = None


def get_planner() -> PlanningManager:
    """获取全局唯一的 PlanningManager 实例。"""
    global _planner
    if _planner is None:
        _planner = PlanningManager()
    return _planner


def reset_planner() -> None:
    """重置规划器（开始新任务时调用）。"""
    global _planner
    _planner = None
