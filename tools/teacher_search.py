"""教师评价搜索工具（Day9+ —— 为 teacher-eval-search skill 提供数据支撑）。

将本地教师评价搜索引擎包装为 Tool，使 Agent 主循环可以调用它来检索教师信息。
"""
from __future__ import annotations
import json
import os
import sys

from tools.base import Tool

# skills/teacher-eval-search 目录名含连字符，无法直接作为 Python 包导入。
# 将其加入 sys.path 以导入内部的 search_engine / safety 模块。
_SKILL_DIR = os.path.join(os.path.dirname(__file__), "..", "skills", "teacher-eval-search")
_SKILL_DIR = os.path.abspath(_SKILL_DIR)
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from search_engine import get_engine  # noqa: E402
from safety import verify_citations  # noqa: E402


def _teacher_search(teachers: str, department: str = "", max_reviews: int = 200) -> str:
    """搜索一位或多位教师的评价数据。

    Args:
        teachers: 教师姓名列表，JSON 数组格式，如 '["张老师", "李老师"]'。
        department: 可选，按院系过滤（如"计算机科学与技术学院"）。
        max_reviews: 最多返回多少条评价原文（默认 100，超出会截断）。
    """
    # 解析教师姓名列表（模型可能传 JSON 字符串或逗号分隔的字符串）
    try:
        teacher_list: list[str] = json.loads(teachers)
    except (json.JSONDecodeError, TypeError):
        teacher_list = [t.strip() for t in str(teachers).split(",") if t.strip()]

    if not teacher_list:
        return "[teacher_search] 错误：请提供至少一位教师姓名。"

    engine = get_engine()
    result = engine.search(teacher_list, department=department, max_reviews=max_reviews)

    # 格式化为模型可读的文本
    lines: list[str] = []
    lines.append(f"=== 教师评价检索结果 ===")
    lines.append(f"查询教师：{', '.join(teacher_list)}")
    if department:
        lines.append(f"院系过滤：{department}")
    lines.append(f"匹配教师数：{result['total_matches']}")
    if result["truncated"]:
        lines.append(f"⚠️ 评价数量过多，已截断至 {max_reviews} 条原文")

    for name, entry in result["teachers"].items():
        dept_str = "、".join(entry["departments"])
        lines.append(f"\n--- {name}（{dept_str}）---")
        lines.append(f"共有 {entry['review_count']} 条评价，以下为检索到的内容：\n")
        for r in entry["reviews"]:
            # 每条评价以全局序号开头，方便模型在输出中用 @序号+关键词@ 引用
            lines.append(
                f"[#{r['id']}] {r['date']}  点赞{r['likes']} / 点踩{r['dislikes']}\n"
                f"  {r['content']}\n"
            )

    if not result["teachers"]:
        lines.append("\n未找到匹配的教师。请尝试：")
        lines.append("- 检查教师姓名拼写是否正确")
        lines.append("- 尝试使用教师姓名的部分文字（如只输入姓氏）")
        lines.append("- 使用 department 参数指定院系进行模糊搜索")

    return "\n".join(lines)


# ---- Tool 定义 ----

teacher_search_tool = Tool(
    name="teacher_search",
    description=(
        "搜索本地教师评价数据库，返回指定教师的学生评价原文。"
        "每条评价带有全局唯一序号 [#N]，后续在总结中必须用 @N+关键词@ 格式引用原始评价。"
        "支持同时查询多位教师（用于对比），支持按院系过滤。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "teachers": {
                "type": "string",
                "description": "要查询的教师姓名，JSON 数组格式，如 '[\"张三\", \"李四\"]'。",
            },
            "department": {
                "type": "string",
                "description": "可选，按院系名称过滤（如'计算机科学与技术学院'）。",
            },
            "max_reviews": {
                "type": "integer",
                "description": "最多返回多少条评价原文，默认 200。评价较多时会被截断。评价按日期降序排列，优先返回近期评价。",
            },
        },
        "required": ["teachers"],
    },
    run=_teacher_search,
)
