"""ReAct 主循环（Agent 的心脏）—— 带规划层。

  while 没到最终答复:
      inject_planning_state(messages)       # 每轮注入当前规划
      assistant = backend.chat(messages, tools)
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = execute with retry / reflection
              messages.append(tool_result)
      else:
          if planner.all_done(): return content
          else: nudge → continue

规划层（Day9+ 新增）：
  - 规划状态每轮注入：todo_write/todo_update + 进度可视化
  - 反思注入：连续错误后触发自我审视（有上限）
  - 错误恢复：瞬时错误自动退避重试 + blocked 标记
  - 无进展检测：连续 N 步无进度 → 预警/重规划
  - 有界停止：步数上限（40 步）+ all_done 完成判据

安全加固（Day10+）：
  - 全局工具调用计数器（防止单任务无限消耗资源）
  - 高敏感工具（bash/write/edit）单独配额限制
  - 循环调用检测
  - 权限分级：READ_ONLY / WRITE / EXECUTE / NETWORK 四级控制
  - 注入防护：web_fetch/read 结果做 prompt injection 检测
"""
from __future__ import annotations
import json
import time
from typing import Any, Callable

from tools.base import ToolRegistry
from agent.context import maybe_compact, truncate_observation
from agent.planning import get_planner, reset_planner, TodoStatus
from agent.permission import PermissionChecker, PermissionTier, TOOL_TIER_MAP, get_tier
from tools.sanitize import sanitize_observation


# ── 安全配额 ──────────────────────────────────────────────────────
_MAX_TOTAL_CALLS = 100
_HIGH_RISK_TOOLS = {"bash", "write", "edit"}
_MAX_HIGH_RISK_CALLS = 30
_MAX_CONSECUTIVE_ERRORS = 3
_MAX_REPEAT_CALLS = 5

# ── 规划层配置 ────────────────────────────────────────────────────
_MAX_TURNS = 60                          # 步数预算（比原来 20 多一倍，给多门课分析留空间）
_STEPS_NO_PROGRESS_LIMIT = 5             # 连续无进展步数上限 → 发预警
_STEPS_FORCE_TERMINATE = 10              # 超过此步数无进展 → 强制终止并返回已有内容
_MAX_RETRIES_TRANSIENT = 3               # 瞬时错误自动重试次数
_TRANSIENT_ERROR_TYPES = (TimeoutError, ConnectionError, OSError)

# ── 错误分类 ──────────────────────────────────────────────────────

def _classify_error(error: Exception, tool_name: str) -> str:
    """分析错误类型，给模型提供修复建议。"""
    msg = str(error).lower()

    if isinstance(error, FileNotFoundError) or "no such file" in msg:
        return f"文件不存在——请检查路径拼写，或先用 glob 确认文件位置。"
    if isinstance(error, PermissionError) or "permission" in msg:
        return f"权限不足——该路径在当前工作目录外，无法访问。"
    if isinstance(error, TimeoutError) or "timeout" in msg:
        return f"操作超时——尝试减小范围或增加 timeout 参数。"
    if isinstance(error, TypeError) or "missing" in msg or "required" in msg or "argument" in msg:
        return f"调用参数错误——缺少必需参数或参数类型不正确。请检查工具 {tool_name} 的参数定义。"
    if "connection" in msg or "refused" in msg:
        return f"网络连接失败——检查 URL 是否正确，或稍后重试。"
    if "not found" in msg:
        return f"资源未找到——检查工具名称或参数是否正确。"
    return f"请分析错误信息，调整参数或策略后重试。"


def _is_transient(error: Exception) -> bool:
    """判断是否为瞬时错误（可自动重试）。"""
    if isinstance(error, _TRANSIENT_ERROR_TYPES):
        return True
    msg = str(error).lower()
    return any(kw in msg for kw in ("timeout", "connection refused", "connection reset",
                                    "temporary failure", "rate limit", "too many requests",
                                    "503", "502", "429"))


def _find_last_detailed_assistant(messages: list[dict[str, Any]]) -> str | None:
    """从消息历史中找最后一条有实质内容的 assistant 消息。

    当模型最终输出太短时（例如只说"完成了"），回溯历史找它之前
    可能已经生成过的详细分析文本。阈值：至少 300 字符或含 Markdown 标记。
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) >= 300 or any(h in content for h in ["##", "###", "**", "---"]):
            return content
    return None


def _was_teacher_search_used(messages: list[dict[str, Any]]) -> bool:
    """检查消息历史中是否调用过 teacher_search 工具。"""
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                if tc.get("name") == "teacher_search":
                    return True
        # 也检查 tool 消息（以防 assistant tool_calls 被压缩）
        if msg.get("role") == "tool" and msg.get("name") == "teacher_search":
            return True
    return False


def _looks_like_checklist_only(content: str) -> bool:
    """检测最终输出是否只是一个进度清单，而非真正的评价报告。

    覆盖三种偷懒模式：
    1. ✅ + 已完成/完成总结（原有逻辑）
    2. 「已输出/已生成 X」但没有实际给出 X（"声称输出"幻觉）
    3. 「任务完成总结」「完成总结」开头但内容极短、无引用
    """
    stripped = content.strip()

    # ── 模式 1：传统清单格式（✅ 标记 + 完成声明）──
    has_checklist_emoji = (
        "✅" in stripped
        and ("已完成" in stripped or "完成总结" in stripped)
    )

    # ── 模式 2：「已输出/已生成 X」但实际没有给出内容 ──
    # 模型说"已输出完整对比报告"，但消息里没有每位教师的评价细节
    claimed_output = (
        ("已输出" in stripped or "已生成" in stripped or "已呈现" in stripped)
        and ("报告" in stripped or "总结" in stripped or "对比" in stripped)
    )
    # 如果声称输出了报告，但：
    #   - 没有引用标签（@N+关键词@），且
    #   - 内容很短（< 1200 chars，真正报告远超此长度）
    # 那就是典型的「说了做了但没做」
    has_citations = "@" in stripped and any(
        c.isdigit() for c in stripped.split("@")[1] if stripped.count("@") >= 2
    ) if "@" in stripped else False
    claimed_but_empty = claimed_output and not has_citations and len(stripped) < 1200

    # ── 模式 3：「任务完成总结」开头的简略输出 ──
    looks_like_summary_header = (
        stripped.startswith("任务完成总结")
        or stripped.startswith("完成总结")
        or stripped.startswith("# 任务完成")
        or stripped.startswith("# 完成总结")
    )
    summary_too_short = looks_like_summary_header and len(stripped) < 1200

    # ── 综合判断 ──
    is_checklist = has_checklist_emoji or claimed_but_empty or summary_too_short

    # 太短（清单通常很短，而真实评价报告会很长）
    is_short = len(stripped) < 800

    return is_checklist and (not has_citations or is_short)


def _build_checklist_nudge(content: str) -> str:
    """根据偷懒类型生成针对性的提示消息。"""
    stripped = content.strip()

    if ("已输出" in stripped or "已生成" in stripped) and not (
        "@" in stripped and any(c.isdigit() for c in stripped.split("@")[1] if stripped.count("@") >= 2)
    ):
        return (
            "你说「已输出/已生成」了报告，但实际上**并没有**把详细内容写出来。\n\n"
            "用户看到的消息里只有一句推荐结论，没有每位教师的评价细节。\n\n"
            "请根据之前 teacher_search 返回的所有原始数据，**逐位教师**写出详细评价，包含：\n"
            "- 教学风格\n- 作业量\n- 考试难度\n- 给分情况\n\n"
            "每位教师的评价后必须跟 @序号+关键词@ 引用标签。\n"
            "⚠️ 不要写「任务完成清单」或「已完成总结」，直接写评价报告正文。现在就写。"
        )

    return (
        "你只输出了一份任务完成清单（「已完成 N 项」），但用户需要看到的是**每门课每位教师的具体评价内容**。\n\n"
        "请根据之前 teacher_search 返回的所有数据，逐门课、逐位教师写出详细总结，"
        "包含每位教师的教学风格、作业量、考试难度、给分情况，并使用 @序号+关键词@ 格式精确引用。\n\n"
        "⚠️ 不要再写「任务完成清单」，直接写评价报告正文。现在就写。"
    )


class AgentLoop:
    def __init__(
        self,
        backend: Any,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = _MAX_TURNS,
        tracer: Any = None,
        permission_callback: Callable[[str, PermissionTier, dict], bool] | None = None,
    ):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.tracer = tracer
        self.permission_checker = PermissionChecker()

        # ── 设置权限询问回调 ──
        # permission_callback 签名：(tool_name, tier, arguments) -> bool（True=允许）
        if permission_callback is not None:
            self.permission_checker.set_high_risk_callback(permission_callback)
        else:
            # 默认回调：通过 input() 在终端询问用户
            self.permission_checker.set_high_risk_callback(
                self._default_permission_prompt
            )

    @staticmethod
    def _default_permission_prompt(tool_name: str, tier: PermissionTier, arguments: dict) -> bool:
        """默认权限询问：使用 input() 直接交互，适用于单次执行和平文本模式。"""
        from .permission import TIER_LABELS
        import json as _json
        label = TIER_LABELS.get(tier, "未知")
        args_str = _json.dumps(arguments, ensure_ascii=False)
        if len(args_str) > 120:
            args_str = args_str[:117] + "..."
        try:
            resp = input(
                f"\n⚠️ [{label}] Agent 要执行 {tool_name}({args_str})"
                f"\n是否允许？[y/N] "
            ).strip().lower()
            return resp in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    # ── 单次执行模式 ────────────────────────────────────────────

    def run(self, user_task: str) -> str:
        # 开始新任务 → 重置规划器
        planner = get_planner()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]

        total_calls = 0
        high_risk_calls = 0
        consecutive_errors = 0
        step = 0
        _recent_call_sigs: list[str] = []

        # ── 规划层状态 ──
        planning_msg_idx: int | None = None          # planning message 在 messages 中的位置
        steps_without_progress = 0                   # 连续无进展步数
        no_progress_warned = False                   # 是否已预警
        completed_before = 0                         # 用于检测进度

        for turn in range(self.max_turns):
            step += 1

            # ── ① 规划状态注入 ──
            planning_text = planner.format_for_prompt()
            if planning_text:
                planning_line = f"## 📋 当前规划状态\n{planning_text}"
                if planning_msg_idx is None:
                    # 首次：插入到 system prompt 之后
                    messages.insert(1, {"role": "system", "content": planning_line})
                    planning_msg_idx = 1
                else:
                    messages[planning_msg_idx] = {"role": "system", "content": planning_line}

            # ── ② 反思注入：连续错误后触发 ──
            current = planner.get_current()
            if (consecutive_errors >= 2
                    and current is not None
                    and current.reflection_count < planner.max_reflections):
                planner.record_reflection(current.id)
                reflection_msg = (
                    f"⚠️ **[反思]** 你在推进 `[#{current.id}]` {current.title} 时遇到了连续错误。\n\n"
                    f"请分析：\n"
                    f"1. 刚才出了什么错？\n"
                    f"2. 当前子任务的真正目标是什么？\n"
                    f"3. 换一种方式（不同参数/不同工具/不同路径）能否达到同样目标？\n"
                    f"4. 如果确实无法完成，请将此项标记为 `blocked` 并继续下一步。\n\n"
                    f"（第 {current.reflection_count}/{planner.max_reflections} 次反思）"
                )
                messages.append({"role": "user", "content": reflection_msg})
                consecutive_errors = 0  # 重置计数，给模型一次机会
                continue  # 不走 API 调用——反思是元步骤

            # ── ③ 无进展预警 / 强制终止 ──
            if steps_without_progress >= _STEPS_NO_PROGRESS_LIMIT and not no_progress_warned:
                no_progress_warned = True
                planner_text = planner.format_for_prompt() or "（未创建待办）"
                warn_msg = (
                    f"⚠️ **[进度预警]** 已经连续 {steps_without_progress} 步没有推进待办进度了。\n\n"
                    f"当前规划状态：\n{planner_text}\n\n"
                    f"建议：\n"
                    f"1. 查看当前规划，确认下一步应该做什么\n"
                    f"2. 如果已获取到足够的 teacher_search 数据，请直接用 todo_update 标记完成并输出总结\n"
                    f"3. 如果某子任务卡住，换个方法或标记 `blocked`\n"
                    f"4. 如果已完成，请直接给出最终总结"
                )
                messages.append({"role": "user", "content": warn_msg})

            # 无进展已达强制终止线 → 自动收尾
            if steps_without_progress >= _STEPS_FORCE_TERMINATE:
                # 自动完成所有待办
                forced_count = 0
                for item in planner.pending_or_in_progress():
                    planner.update(item.id, "completed",
                                   f"强制完成（无进展 {steps_without_progress} 步）")
                    forced_count += 1
                planner_text = planner.format_for_prompt()
                if planner_text:
                    messages[planning_msg_idx] = {"role": "system", "content": planner_text}
                terminate_msg = (
                    f"⏰ [系统] 已经连续 {steps_without_progress} 步没有实质进展，"
                    + f"已自动完成 {forced_count} 项待办。\n"
                    + f"请根据目前已获取的所有数据，直接输出最终总结，不要再调用任何搜索工具。"
                )
                messages.append({"role": "user", "content": terminate_msg})

            # ── ④ API 调用 ──
            assistant = self.backend.chat(
                messages,
                tools=self.registry.schemas(),
            )

            # ── 可观测性：记录轨迹 ──
            if self.tracer is not None:
                usage = assistant.get("usage", {})
                tool_names = [tc["name"] for tc in (assistant.get("tool_calls") or [])]
                self.tracer.log_step(
                    step=step,
                    tool_calls=assistant.get("tool_calls") or [],
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    note="; ".join(tool_names) if tool_names else "最终答复",
                )

            messages.append({
                "role": "assistant",
                "content": assistant.get("content") or "",
                "tool_calls": assistant.get("tool_calls") or [],
            })

            tool_calls = assistant.get("tool_calls") or []

            # ── ⑤ 无工具调用 → 完成检查 ──
            if not tool_calls:
                _recent_call_sigs.clear()
                if planner.all_done():
                    content = assistant.get("content", "")
                    # ── ① 超短输出检测 ──
                    if len(content) < 300 and not any(h in content for h in ["##", "###", "**", "---"]):
                        detailed = _find_last_detailed_assistant(messages)
                        if detailed:
                            return detailed
                        messages.append({
                            "role": "user",
                            "content": (
                                "你刚才只写了一句简短的完成声明，但还没有把实际的详细总结输出给我。\n\n"
                                "请根据之前收集到的所有数据，直接输出一份完整的总结报告，包含：\n"
                                "- 每门课程的授课教师\n"
                                "- 每位教师的评价摘要（含学生评价要点）\n"
                                "- 引用标签（@序号+关键词@）\n\n"
                                "现在就写，不要再调用任何工具。"
                            ),
                        })
                        continue
                    # ── ② 清单式输出检测：模型写了「已完成 X/Y」清单但没有实际评价内容 ──
                    if _was_teacher_search_used(messages) and _looks_like_checklist_only(content):
                        messages.append({
                            "role": "user",
                            "content": _build_checklist_nudge(content),
                        })
                        continue
                    return content
                # 还有未完成的待办 → 提示继续
                pending = planner.pending_or_in_progress()
                if pending:
                    nudge = (
                        f"⚠️ 当前还有 **{len(pending)} 项待办**未完成，请继续推进：\n"
                        + "\n".join(
                            f"  • `[#{t.id}]` {t.title}"
                            + ("（受阻）" if t.status == TodoStatus.BLOCKED else "")
                            for t in pending
                        )
                    )
                    messages.append({"role": "user", "content": nudge})
                    continue
                content = assistant.get("content", "")
                # ── ① 超短输出检测 ──
                if len(content) < 300 and not any(h in content for h in ["##", "###", "**", "---"]):
                    detailed = _find_last_detailed_assistant(messages)
                    if detailed:
                        return detailed
                    messages.append({
                        "role": "user",
                        "content": (
                            "你刚才只写了一句简短的完成声明，但还没有把实际的详细总结输出给我。\n\n"
                            "请根据之前收集到的所有数据，直接输出一份完整的总结报告，包含：\n"
                            "- 每门课程的授课教师\n"
                            "- 每位教师的评价摘要（含学生评价要点）\n"
                            "- 引用标签（@序号+关键词@）\n\n"
                            "现在就写，不要再调用任何工具。"
                        ),
                    })
                    continue
                # ── ② 清单式输出检测 ──
                if _was_teacher_search_used(messages) and _looks_like_checklist_only(content):
                    messages.append({
                        "role": "user",
                        "content": _build_checklist_nudge(content),
                    })
                    continue
                return content

            # ── 重复调用检测 ──
            for call in tool_calls:
                sig = f"{call['name']}:{json.dumps(call.get('arguments', {}), sort_keys=True, ensure_ascii=False)}"
                _recent_call_sigs.append(sig)
            if len(_recent_call_sigs) > _MAX_REPEAT_CALLS:
                _recent_call_sigs.pop(0)

            # ── ⑥ 执行工具（含瞬时错误重试） ──
            had_error = False
            for call in tool_calls:
                # 配额检查
                total_calls += 1
                if total_calls > _MAX_TOTAL_CALLS:
                    obs = f"[安全配额] 已达到全局工具调用上限（{_MAX_TOTAL_CALLS} 次），拒绝执行 {call['name']}"
                    messages.append({
                        "role": "tool", "name": call["name"],
                        "tool_call_id": call.get("id"), "content": obs,
                    })
                    continue

                if call["name"] in _HIGH_RISK_TOOLS:
                    high_risk_calls += 1
                    if high_risk_calls > _MAX_HIGH_RISK_CALLS:
                        obs = f"[安全配额] 高风险工具 '{call['name']}' 已达到配额上限（{_MAX_HIGH_RISK_CALLS} 次），拒绝执行"
                        messages.append({
                            "role": "tool", "name": call["name"],
                            "tool_call_id": call.get("id"), "content": obs,
                        })
                        continue

                # ── 权限分级检查 ──
                if get_tier(call["name"]) != PermissionTier.READ_ONLY:
                    allowed, reason = self.permission_checker.check(
                        call["name"], call.get("arguments", {}),
                    )
                    if not allowed:
                        obs = reason
                        messages.append({
                            "role": "tool", "name": call["name"],
                            "tool_call_id": call.get("id"),
                            "content": truncate_observation(str(obs)),
                        })
                        continue

                tool = self.registry.get(call["name"])

                if tool is None:
                    obs = f"错误：未知工具 {call['name']}。可用工具：{', '.join(self.registry.names())}"
                    consecutive_errors += 1
                    had_error = True
                else:
                    # ── 瞬时错误自动重试（退避） ──
                    obs, success = self._run_with_retry(tool, call)
                    if success:
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1
                        had_error = True

                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    obs += f"\n\n⚠️ 已连续 {consecutive_errors} 次错误，任务可能无法继续。"

                messages.append({
                    "role": "tool", "name": call["name"],
                    "tool_call_id": call.get("id"),
                    "content": truncate_observation(str(obs)),
                })

            # ── ⑦ 进度检测 ──
            completed_after = planner.completed_count()
            if completed_after > completed_before:
                steps_without_progress = 0
                no_progress_warned = False
            else:
                steps_without_progress += 1
            completed_before = completed_after

            # ── ⑧ 循环检测 ──
            if len(_recent_call_sigs) >= _MAX_REPEAT_CALLS and len(set(_recent_call_sigs)) == 1:
                stuck_call = tool_calls[0]["name"]
                stuck_args = tool_calls[0].get("arguments", {})
                _already_warned = any(
                    msg.get("role") == "user" and "[循环检测]" in str(msg.get("content", ""))
                    for msg in messages[-3:]
                )
                if _already_warned:
                    return (
                        f"[检测到模型陷入循环] 连续 {_MAX_REPEAT_CALLS} 次调用 "
                        f"{stuck_call}({json.dumps(stuck_args, ensure_ascii=False)})，"
                        f"注入提示后仍未纠正，自动终止。"
                    )
                hint = (
                    f"⚠️ [循环检测] 你已经连续 {_MAX_REPEAT_CALLS} 次调用了相同的 "
                    f"{stuck_call}({json.dumps(stuck_args, ensure_ascii=False)})。"
                    f"请立即改变策略。"
                )
                messages.append({"role": "user", "content": hint})
                _recent_call_sigs.clear()

            # ── ⑨ 上下文压缩 ──
            messages = maybe_compact(messages, self.backend)

        # ── ⑩ 步数预算耗尽 ──
        summary = planner.format_short_summary()
        if summary:
            return (
                f"[达到最大轮数上限] 任务未完成。{summary}\n\n"
                f"已完成的工作见上方输出。剩余待办已保存在规划器中，"
                f"如有需要可继续提问。"
            )
        return "[达到最大轮数上限，未完成任务]"

    # ── 流式执行模式 ────────────────────────────────────────────

    def run_stream(self, user_task: str, messages: list[dict[str, Any]] | None = None):
        """Generator yielding (event_type, data) tuples for real-time display.

        Yields:
          ('thinking', {})
          ('tool_call', {'name': str, 'arguments': dict, 'id': str})
          ('tool_result', {'name': str, 'result': str, 'success': bool, 'id': str})
          ('done', {'content': str, 'messages': list})
          ('error', {'message': str})
        """
        planner = get_planner()

        if messages is None:
            messages = [
                {"role": "system", "content": self.system_prompt},
            ]
        if (not messages or
                messages[-1].get("role") != "user" or
                messages[-1].get("content") != user_task):
            messages.append({"role": "user", "content": user_task})

        total_calls = 0
        high_risk_calls = 0
        consecutive_errors = 0
        step = 0
        _recent_call_sigs: list[str] = []

        planning_msg_idx: int | None = None
        steps_without_progress = 0
        no_progress_warned = False
        completed_before = 0

        for _turn in range(self.max_turns):
            step += 1

            # ── ① 规划状态注入 ──
            planning_text = planner.format_for_prompt()
            if planning_text:
                planning_line = f"## 📋 当前规划状态\n{planning_text}"
                if planning_msg_idx is None:
                    messages.insert(1, {"role": "system", "content": planning_line})
                    planning_msg_idx = 1
                else:
                    messages[planning_msg_idx] = {"role": "system", "content": planning_line}

            # ── ② 反思注入 ──
            current = planner.get_current()
            if (consecutive_errors >= 2
                    and current is not None
                    and current.reflection_count < planner.max_reflections):
                planner.record_reflection(current.id)
                reflection_msg = (
                    f"⚠️ **[反思]** 你在推进 `[#{current.id}]` {current.title} 时遇到了连续错误。\n\n"
                    f"请分析：\n"
                    f"1. 刚才出了什么错？2. 当前子任务的真正目标是什么？"
                    f"3. 换一种方式能否达到同样目标？"
                    f"4. 如果无法完成，标记为 `blocked` 继续下一步。\n\n"
                    f"（第 {current.reflection_count}/{planner.max_reflections} 次反思）"
                )
                messages.append({"role": "user", "content": reflection_msg})
                consecutive_errors = 0
                continue

            # ── ③ 无进展预警 / 强制终止 ──
            if steps_without_progress >= _STEPS_NO_PROGRESS_LIMIT and not no_progress_warned:
                no_progress_warned = True
                planner_text = planner.format_for_prompt() or "（未创建待办）"
                warn_msg = (
                    f"⚠️ **[进度预警]** 已经连续 {steps_without_progress} 步没有推进待办进度了。\n\n"
                    f"当前规划：{planner_text}\n\n"
                    f"建议：查看规划确认下一步，卡住的标 blocked，完成了就总结。"
                )
                messages.append({"role": "user", "content": warn_msg})

            # 无进展已达强制终止线 → 自动收尾
            if steps_without_progress >= _STEPS_FORCE_TERMINATE:
                forced_count = 0
                for item in planner.pending_or_in_progress():
                    planner.update(item.id, "completed",
                                   f"强制完成（无进展 {steps_without_progress} 步）")
                    forced_count += 1
                planner_text = planner.format_for_prompt()
                if planner_text and planning_msg_idx is not None:
                    messages[planning_msg_idx] = {"role": "system", "content": planner_text}
                terminate_msg = (
                    f"⏰ [系统] 已经连续 {steps_without_progress} 步没有实质进展，"
                    + f"已自动完成 {forced_count} 项待办。"
                    + f"请根据目前已获取的所有数据，直接输出最终总结，不要再调用任何搜索工具。"
                )
                messages.append({"role": "user", "content": terminate_msg})

            # ── ④ API 调用 ──
            yield ("thinking", {})

            assistant = self.backend.chat(
                messages,
                tools=self.registry.schemas(),
            )

            if self.tracer is not None:
                usage = assistant.get("usage", {})
                tool_names = [tc["name"] for tc in (assistant.get("tool_calls") or [])]
                self.tracer.log_step(
                    step=step,
                    tool_calls=assistant.get("tool_calls") or [],
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    note="; ".join(tool_names) if tool_names else "最终答复",
                )

            messages.append({
                "role": "assistant",
                "content": assistant.get("content") or "",
                "tool_calls": assistant.get("tool_calls") or [],
            })

            tool_calls = assistant.get("tool_calls") or []

            # ── ⑤ 完成检查 ──
            if not tool_calls:
                _recent_call_sigs.clear()
                if planner.all_done():
                    content = assistant.get("content", "")
                    # ── ① 超短输出检测 ──
                    if len(content) < 300 and not any(h in content for h in ["##", "###", "**", "---"]):
                        detailed = _find_last_detailed_assistant(messages)
                        if detailed:
                            yield ("done", {
                                "content": detailed,
                                "messages": messages,
                            })
                            return
                        messages.append({
                            "role": "user",
                            "content": (
                                "你刚才只写了一句简短的完成声明，但还没有把实际的详细总结输出给我。\n\n"
                                "请根据之前收集到的所有数据，直接输出一份完整的总结报告，包含：\n"
                                "- 每门课程的授课教师\n"
                                "- 每位教师的评价摘要（含学生评价要点）\n"
                                "- 引用标签（@序号+关键词@）\n\n"
                                "现在就写，不要再调用任何工具。"
                            ),
                        })
                        continue
                    # ── ② 清单式输出检测 ──
                    if _was_teacher_search_used(messages) and _looks_like_checklist_only(content):
                        messages.append({
                            "role": "user",
                            "content": _build_checklist_nudge(content),
                        })
                        continue
                    yield ("done", {
                        "content": content,
                        "messages": messages,
                    })
                    return
                pending = planner.pending_or_in_progress()
                if pending:
                    nudge = (
                        f"⚠️ 当前还有 **{len(pending)} 项待办**未完成，请继续推进：\n"
                        + "\n".join(
                            f"  • `[#{t.id}]` {t.title}"
                            + ("（受阻）" if t.status == TodoStatus.BLOCKED else "")
                            for t in pending
                        )
                    )
                    messages.append({"role": "user", "content": nudge})
                    continue
                content = assistant.get("content", "")
                # ── ① 超短输出检测 ──
                if len(content) < 300 and not any(h in content for h in ["##", "###", "**", "---"]):
                    detailed = _find_last_detailed_assistant(messages)
                    if detailed:
                        yield ("done", {
                            "content": detailed,
                            "messages": messages,
                        })
                        return
                    messages.append({
                        "role": "user",
                        "content": (
                            "你刚才只写了一句简短的完成声明，但还没有把实际的详细总结输出给我。\n\n"
                            "请根据之前收集到的所有数据，直接输出一份完整的总结报告，包含：\n"
                            "- 每门课程的授课教师\n"
                            "- 每位教师的评价摘要（含学生评价要点）\n"
                            "- 引用标签（@序号+关键词@）\n\n"
                            "现在就写，不要再调用任何工具。"
                        ),
                    })
                    continue
                # ── ② 清单式输出检测 ──
                if _was_teacher_search_used(messages) and _looks_like_checklist_only(content):
                    messages.append({
                        "role": "user",
                        "content": _build_checklist_nudge(content),
                    })
                    continue
                yield ("done", {
                    "content": content,
                    "messages": messages,
                })
                return

            # ── 重复调用检测 ──
            for call in tool_calls:
                sig = f"{call['name']}:{json.dumps(call.get('arguments', {}), sort_keys=True, ensure_ascii=False)}"
                _recent_call_sigs.append(sig)
            if len(_recent_call_sigs) > _MAX_REPEAT_CALLS:
                _recent_call_sigs.pop(0)

            # ── ⑥ 执行工具 ──
            had_error = False
            for call in tool_calls:
                total_calls += 1
                if total_calls > _MAX_TOTAL_CALLS:
                    obs = f"[安全配额] 已达到全局工具调用上限（{_MAX_TOTAL_CALLS} 次），拒绝执行 {call['name']}"
                    messages.append({
                        "role": "tool", "name": call["name"],
                        "tool_call_id": call.get("id"), "content": obs,
                    })
                    yield ("tool_result", {
                        "name": call["name"], "result": obs,
                        "success": False, "id": call.get("id"),
                    })
                    continue

                if call["name"] in _HIGH_RISK_TOOLS:
                    high_risk_calls += 1
                    if high_risk_calls > _MAX_HIGH_RISK_CALLS:
                        obs = f"[安全配额] 高风险工具 '{call['name']}' 已达到配额上限（{_MAX_HIGH_RISK_CALLS} 次），拒绝执行"
                        messages.append({
                            "role": "tool", "name": call["name"],
                            "tool_call_id": call.get("id"), "content": obs,
                        })
                        yield ("tool_result", {
                            "name": call["name"], "result": obs,
                            "success": False, "id": call.get("id"),
                        })
                        continue

                # ── 权限分级检查（提前到 yield tool_call 之前）──
                if get_tier(call["name"]) != PermissionTier.READ_ONLY:
                    allowed, reason = self.permission_checker.check(
                        call["name"], call.get("arguments", {}),
                    )
                    if not allowed:
                        messages.append({
                            "role": "tool", "name": call["name"],
                            "tool_call_id": call.get("id"), "content": reason,
                        })
                        yield ("tool_result", {
                            "name": call["name"], "result": reason,
                            "success": False, "id": call.get("id"),
                        })
                        continue

                yield ("tool_call", {
                    "name": call["name"],
                    "arguments": call.get("arguments", {}),
                    "id": call.get("id"),
                })

                tool = self.registry.get(call["name"])

                if tool is None:
                    obs = f"错误：未知工具 {call['name']}。可用工具：{', '.join(self.registry.names())}"
                    consecutive_errors += 1
                    success = False
                else:
                    obs, success = self._run_with_retry(tool, call)
                    if success:
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1

                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    obs += f"\n\n⚠️ 已连续 {consecutive_errors} 次错误，任务可能无法继续。"

                messages.append({
                    "role": "tool", "name": call["name"],
                    "tool_call_id": call.get("id"),
                    "content": truncate_observation(str(obs)),
                })

                yield ("tool_result", {
                    "name": call["name"],
                    "result": truncate_observation(str(obs)),
                    "success": success,
                    "id": call.get("id"),
                })

            # ── ⑦ 进度检测 ──
            completed_after = planner.completed_count()
            if completed_after > completed_before:
                steps_without_progress = 0
                no_progress_warned = False
            else:
                steps_without_progress += 1
            completed_before = completed_after

            # ── ⑧ 循环检测 ──
            if len(_recent_call_sigs) >= _MAX_REPEAT_CALLS and len(set(_recent_call_sigs)) == 1:
                stuck_call = tool_calls[0]["name"]
                stuck_args = tool_calls[0].get("arguments", {})
                _already_warned = any(
                    msg.get("role") == "user" and "[循环检测]" in str(msg.get("content", ""))
                    for msg in messages[-3:]
                )
                if _already_warned:
                    yield ("done", {
                        "content": (
                            f"[检测到模型陷入循环] 连续 {_MAX_REPEAT_CALLS} 次调用 "
                            f"{stuck_call}({json.dumps(stuck_args, ensure_ascii=False)})，自动终止。"
                        ),
                        "messages": messages,
                    })
                    return
                hint = (
                    f"⚠️ [循环检测] 你已经连续 {_MAX_REPEAT_CALLS} 次调用了相同的 "
                    f"{stuck_call}({json.dumps(stuck_args, ensure_ascii=False)})。请立即改变策略。"
                )
                messages.append({"role": "user", "content": hint})
                _recent_call_sigs.clear()

            # ── ⑨ 上下文压缩 ──
            messages = maybe_compact(messages, self.backend)

        # ── ⑩ 步数预算耗尽 ──
        summary_text = planner.format_short_summary()
        if summary_text:
            content = f"[达到最大轮数上限] 任务未完成。{summary_text}"
        else:
            content = "[达到最大轮数上限，未完成任务]"
        yield ("done", {"content": content, "messages": messages})

    # ── 辅助方法 ────────────────────────────────────────────────

    def _run_with_retry(
        self, tool: Any, call: dict[str, Any]
    ) -> tuple[str, bool]:
        """执行工具，瞬时错误自动重试（指数退避） + 注入防护检测。"""
        last_error = None
        for attempt in range(_MAX_RETRIES_TRANSIENT):
            try:
                obs = tool.run(**call.get("arguments", {}))
                # ── 注入防护检测（web_fetch / read 来源）──
                if tool.name in ("web_fetch", "read"):
                    source = "web" if tool.name == "web_fetch" else "file"
                    obs = sanitize_observation(str(obs), source=source)
                return truncate_observation(str(obs)), True
            except Exception as e:
                last_error = e
                if _is_transient(e) and attempt < _MAX_RETRIES_TRANSIENT - 1:
                    sleep_sec = 2 ** attempt  # 退避：1s, 2s, 4s
                    wait_msg = (
                        f"[自动重试 {attempt + 1}/{_MAX_RETRIES_TRANSIENT}] "
                        f"工具 {tool.name} 遇到瞬时错误：{e}，{sleep_sec} 秒后重试..."
                    )
                    time.sleep(sleep_sec)
                    # 退避期间不向 messages 写任何东西——模型不需要看到中间重试
                else:
                    break

        # 所有重试都失败，或遇到非瞬时错误
        hint = _classify_error(last_error, tool.name)
        obs = f"工具 {tool.name} 执行出错：{last_error}\n[修复建议] {hint}"

        # ── 自动标记 blocked ──
        planner = get_planner()
        current = planner.get_current()
        if current is not None and not _is_transient(last_error):
            planner.increment_error(current.id)
            if planner.is_stuck(current.id):
                planner.update(current.id, "blocked",
                               f"重试 {_MAX_RETRIES_TRANSIENT} 次后仍失败：{last_error}")
                obs += (
                    f"\n\n🚧 [自动处理] 子任务 `[#{current.id}]` {current.title} "
                    f"已自动标记为 **blocked**（错误/反思超限），将继续推进下一步。"
                )

        return obs, False
