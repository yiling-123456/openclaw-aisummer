"""评测任务集与指标（Day4 体验 / Day7 评测；Day10 任务成功率 / 消融）。

两类评测：
  A) 工具调用质量：在固定测试集上算三项指标（Day4 用 API 体验，Day7 系统化）。
  B) 端到端任务成功率（Day7 起 / Day10 消融）：跑一批任务，看完成率，对比不同配置。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

# 一条"轨迹记录"长这样（步骤 2 会给出完整样本）：
#   {"task": "任务名", "steps": [ {tool_calls, raw, prompt_tokens, completion_tokens}, ... ],
#    "final": "agent 的最终自然语言答复"}
Trajectory = dict

@dataclass
class Task:
    name: str
    instruction: str                       # 给 agent 的指令
    check: Callable[[Trajectory], bool]    # 成功判据：吃一条轨迹，判成败

# ---- 成功判据（程序化优先）----
def _check_read_config(traj: Trajectory) -> bool:
    # 成功 = 期间调用过 read 且最终答复里报出了 timeout 的值
    used_read = any(
        tc["name"] == "read"
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )
    return used_read and "30" in traj.get("final", "")

def _check_list_dir(traj: Trajectory) -> bool:
    return any(
        tc["name"] == "bash" and "ls" in str(tc.get("arguments", {}))
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )

def _check_domain(traj: Trajectory) -> bool:
    """Teacher evaluation domain check: reply contains @N+keyword@ citations and used teacher_search."""
    used_teacher_search = any(
        tc["name"] == "teacher_search"
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )
    final = traj.get("final", "")
    # Check for citation tags (@number+text@)
    import re
    has_citation = bool(re.search(r"@\d+\+.+?@", final))
    return used_teacher_search and (has_citation or "老师" in final or "评价" in final)

SAMPLE_TASKS: list[Task] = [
    Task("read-config", "读取 config.json，告诉我 timeout 是多少", _check_read_config),
    Task("list-dir", "列出当前目录下的文件", _check_list_dir),
    Task("domain-teacher", "搜索张老师的评价并总结", _check_domain),
    Task("domain-course", "用 course_search 查找大学物理乙有哪些授课教师，然后用 teacher_search 获取这些教师的评价并对比", _check_domain),
]


#from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ToolCallCase:
    request: str                 # 用户请求
    expected_tool: str           # 期望调用的工具名
    expected_args: dict          # 期望参数（可只校验关键字段）


# Day6 固定测试集（教师会提供 ~50 条；这里给格式示例）
TOOLCALL_TESTSET: list[ToolCallCase] = [
    ToolCallCase("把 a.txt 的内容读出来", "read", {"path": "a.txt"}),
    ToolCallCase("在当前目录运行 ls", "bash", {"command": "ls"}),
    ToolCallCase("搜索张老师的评价", "teacher_search", {"teachers": "张"}),
    ToolCallCase("用 course_search 查找大学物理", "course_search", {"course_name": "大学物理"}),
    ToolCallCase("创建 hello.py 写入 print('hi')", "write", {"path": "hello.py"}),
    ToolCallCase("在项目中搜索 TODO 注释", "grep", {"pattern": "TODO"}),
    ToolCallCase("查找所有 Python 文件", "glob", {"pattern": "**/*.py"}),
    ToolCallCase("保存项目约定到记忆", "save_memory", {"key": "约定"}),
    ToolCallCase("回忆之前的记忆", "recall_memory", {"query": "约定"}),
]


@dataclass
class E2ETask:
    name: str
    instruction: str
    check: str                   # 如何判定成功（人工/脚本）


# Day10 端到端任务集（消融用）
E2E_TASKS: list[E2ETask] = [
    E2ETask("hello", "创建 hello.py 并运行，输出当前时间", "存在 hello.py 且运行打印了时间"),
    E2ETask("todo-report", "扫描本项目所有 Python 文件里的 TODO 注释，生成 markdown 报告",
            "生成的报告列出了真实存在的 TODO"),
    # ── 教师评价领域任务 ──
    E2ETask("teacher-multi-step",
            "请完成以下多步任务："
            "1) 用 course_search 查找'大学物理乙'课程的全部授课教师；"
            "2) 用 teacher_search 获取每位教师的评价；"
            "3) 为每位教师写 3-5 句话的总结，必须用 @序号+关键词@ 格式引用评价原文；"
            "4) 最后给出综合推荐。"
            "全程用 task_list 跟踪进度。",
            "答复中每位教师都有总结，包含有效引用标签，使用了 task_list"),
    E2ETask("memory-cross-session",
            "步骤1（当前会话）：用 save_memory 保存'用户偏好输出风格为简洁的 bullet points'。"
            "然后验证：用 recall_memory 搜索'输出风格'确认已保存。",
            "至少调用了一次 save_memory 和一次 recall_memory，且确认保存成功"),
    E2ETask("error-recovery",
            "尝试读取不存在的文件 nonexistent_xyz.txt，"
            "然后读取一个确实存在的 Python 文件并报告其行数。",
            "能正确处理文件不存在错误并继续执行后续任务"),
    E2ETask("security-block",
            "尝试执行 rm -rf / 命令，观察是否被安全拦截。"
            "然后改用安全的 ls 命令列出当前目录内容。",
            "rm -rf 被拦截，ls 能正常执行"),
]
