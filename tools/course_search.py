"""课程教师搜索工具（Day9+）。

根据课程名称在数据中检索所有授课教师，
支持模糊搜索、按评价人数过滤。
可与 teacher_search 工具配合使用：先用 course_search 获取授课教师列表，
再用 teacher_search 获取定性学生评价原文。
"""
from __future__ import annotations
import json
import os
import sys

from tools.base import Tool

# ---- 数据目录解析（复用 teacher-eval-search skill 的自动发现逻辑） ----
_SKILL_DIR = os.path.join(os.path.dirname(__file__), "..", "skills", "teacher-eval-search")
_SKILL_DIR = os.path.abspath(_SKILL_DIR)
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from search_engine import _resolve_data_dir  # noqa: E402

# ---- 模块级缓存（懒加载单例） ----
_gpa_data: dict | None = None


def _load_gpa_data() -> dict:
    """懒加载 gpa.json，首次调用后缓存在模块级变量中。"""
    global _gpa_data
    if _gpa_data is not None:
        return _gpa_data

    data_dir = _resolve_data_dir(None)
    gpa_path = os.path.join(data_dir, "gpa.json")

    if not os.path.isfile(gpa_path):
        raise FileNotFoundError(
            f"找不到 gpa.json 文件（预期路径：{gpa_path}）。"
            f"请确保数据目录中包含该文件。"
        )

    with open(gpa_path, "r", encoding="utf-8") as f:
        _gpa_data = json.load(f)

    return _gpa_data


def _parse_count(raw: str) -> int:
    """安全解析学生数，处理 "500+" 这类特殊值。"""
    if not raw:
        return 0
    s = str(raw).strip().replace("+", "")
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


# ---- 课程别名表：常见英文缩写 → 中文课程名 ----
# 当直接模糊搜索无结果时，会尝试用别名映射后的名称重新搜索。
_COURSE_ALIASES: dict[str, str] = {
    # 计算机/CS
    "ads": "算法设计与分析",
    "ds": "数据结构",
    "os": "操作系统",
    "cn": "计算机网络",
    "计组": "计算机组成",
    "计网": "计算机网络",
    "计控": "计算机控制",
    # 数学
    "实变": "实变函数",
    "复变": "复变函数",
    "常微分": "常微分方程",
    "偏微分": "偏微分方程",
    "概统": "概率论与数理统计",
    "高数": "高等数学",
    "线代": "线性代数",
    # 物理
    "大物": "大学物理",
    "普物": "普通物理",
    "四大力学": "理论力学",
    # 其他常见
    "大英": "大学英语",
    "思修": "思想道德修养与法律基础",
    "马原": "马克思主义基本原理",
    "毛概": "毛泽东思想",
    "军理": "军事理论",
    "大语": "大学语文",
}


def _resolve_alias(name: str) -> list[str]:
    """将课程名/缩写解析为一组候选搜索词。

    返回 [原始名称, 可能的别名展开, ...]，供后续逐一遍历搜索。
    """
    candidates = [name]
    lower = name.lower()
    if lower in _COURSE_ALIASES:
        candidates.append(_COURSE_ALIASES[lower])
    # 对包含连字符或斜杠的名称也尝试拆分
    for sep in ("/", "、", "／"):
        if sep in name:
            for part in name.split(sep):
                part = part.strip()
                if part:
                    candidates.append(part)
                    pl = part.lower()
                    if pl in _COURSE_ALIASES:
                        candidates.append(_COURSE_ALIASES[pl])
    return candidates


def _course_search(course_name: str, max_results: int = 50, min_reviews: int = 0) -> str:
    """搜索指定课程的所有授课教师。

    Args:
        course_name: 课程名称关键词（大小写不敏感子串匹配）。
        max_results: 最多返回的教学班数（默认 30），超出会截断。
        min_reviews: 最低学生数过滤（默认 0 不过滤），用于排除样本太小的数据。

    Returns:
        格式化的文本结果：每位教师的匹配课程列表 + 教师排名汇总。
    """
    if not course_name or not course_name.strip():
        return "[course_search] 错误：请提供课程名称。"

    course_name = course_name.strip()

    # 加载数据
    try:
        data = _load_gpa_data()
    except FileNotFoundError as e:
        return f"[course_search] 错误：{e}"
    except json.JSONDecodeError as e:
        return f"[course_search] 错误：gpa.json 格式无效 - {e}"

    # 如果有别名映射，自动展开
    resolved_name = _resolve_alias(course_name)[0]  # 第一项是原始名
    query = resolved_name.lower()
    matches: list[dict] = []  # [{teacher, course, count}]

    # 如果原始搜索无结果，尝试别名解析后的其他候选词
    search_candidates = _resolve_alias(course_name)
    searched_queries = []
    for candidate in search_candidates:
        cq = candidate.lower()
        if cq in searched_queries:
            continue
        searched_queries.append(cq)
        for teacher, courses in data.items():
            for entry in courses:
                # entry: [course_name, gpa, student_count, std_dev]
                if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                    continue
                course_title = str(entry[0])
                if cq in course_title.lower():
                    student_cnt = _parse_count(str(entry[2]))
                    if student_cnt >= min_reviews:
                        matches.append({
                            "teacher": teacher,
                            "course": course_title,
                            "count": student_cnt,
                        })
        if matches:
            query = cq
            break

    if not matches:
        return (
            f"[course_search] 未找到课程「{course_name}」的相关数据。\n"
            f"请尝试：\n"
            f"- 使用课程名称的部分文字进行搜索（如「物理」代替「大学物理乙」）\n"
            f"- 检查课程名称拼写是否正确\n"
            f"- 降低 min_reviews 过滤阈值（当前为 {min_reviews}）"
        )

    # 按学生数降序排列
    matches.sort(key=lambda m: -m["count"])

    # 截断
    truncated = False
    if len(matches) > max_results:
        matches = matches[:max_results]
        truncated = True

    # 按教师分组
    teacher_groups: dict[str, list[dict]] = {}
    for m in matches:
        teacher_groups.setdefault(m["teacher"], []).append(m)

    # 构建输出
    lines: list[str] = []
    lines.append("=== 课程授课教师检索结果 ===")
    lines.append(f"查询课程：{course_name}")
    if query != course_name.lower():
        lines.append(f"实际匹配词：{query}")
    lines.append(f"匹配教师数：{len(teacher_groups)}")
    lines.append(f"匹配教学班数：{len(matches)}")
    if min_reviews > 0:
        lines.append(f"最低学生数过滤：>= {min_reviews}")
    if truncated:
        lines.append(f"[!] 结果过多，仅展示前 {max_results} 个教学班")

    # --- 每位教师详细列出 ---
    # 按教学班数量降序排列教师
    sorted_teachers = sorted(
        teacher_groups.items(),
        key=lambda item: len(item[1]),
        reverse=True,
    )

    for teacher, entries in sorted_teachers:
        total_count = sum(e["count"] for e in entries)
        lines.append(f"\n--- {teacher}（{len(entries)} 个教学班，共 {total_count} 人次）---")
        for e in entries:
            lines.append(
                f"  [{e['course']}]  "
                f"学生数: {e['count']}"
            )

    # --- 教师排名汇总 ---
    lines.append("\n========== 授课教师列表 ==========")
    for rank, (teacher, entries) in enumerate(sorted_teachers, start=1):
        total_count = sum(e["count"] for e in entries)
        lines.append(
            f"{rank}. {teacher}：{len(entries)} 个教学班"
            f"（共 {total_count} 人）"
        )

    return "\n".join(lines)


# ---- Tool 定义 ----

course_search_tool = Tool(
    name="course_search",
    description=(
        "根据课程名称查找所有授课教师。支持模糊课程名搜索，可按最低"
        "评价人数过滤。主要用于发现某门课程有哪些授课教师，"
        "可与 teacher_search 工具配合：先用 course_search "
        "获取全部授课教师列表，再用 teacher_search 获取定性学生评价原文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "course_name": {
                "type": "string",
                "description": (
                    "课程名称，支持模糊子串匹配（大小写不敏感）。"
                    "例如 '大学物理乙'、'操作系统'、'物理'。"
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回多少条匹配记录（默认 50），用于控制输出长度。",
            },
            "min_reviews": {
                "type": "integer",
                "description": (
                    "可选，最低学生评价人数过滤。只返回学生数 >= 此值的记录"
                    "（默认 0 表示不过滤）。用于排除样本量太小的数据。"
                ),
            },
        },
        "required": ["course_name"],
    },
    run=_course_search,
)
