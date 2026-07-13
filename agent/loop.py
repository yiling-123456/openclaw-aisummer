"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)
              messages.append(tool_result(obs))
      else:
          return assistant.content

安全加固（Day10+）：
  - 全局工具调用计数器（防止单任务无限消耗资源）
  - 高敏感工具（bash/write/edit）单独配额限制
"""

from __future__ import annotations
import json
from typing import Any

from tools.base import ToolRegistry
from agent.context import maybe_compact, truncate_observation

# ── 安全配额 ──────────────────────────────────────────────────────
# 单次任务全局工具调用上限
_MAX_TOTAL_CALLS = 100
# 高风险工具单独配额（写 / shell / 编辑）
_HIGH_RISK_TOOLS = {"bash", "write", "edit"}
_MAX_HIGH_RISK_CALLS = 30
# 连续错误上限（防止死循环）
_MAX_CONSECUTIVE_ERRORS = 3

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
        return f"调用参数错误——缺少必需参数或参数类型不正确。请检查工具 {tool_name} 的参数定义，确保传入了所有必填字段。"
    if "connection" in msg or "refused" in msg:
        return f"网络连接失败——检查 URL 是否正确，或稍后重试。"
    if "not found" in msg:
        return f"资源未找到——检查工具名称或参数是否正确。"
    return f"请分析错误信息，调整参数或策略后重试。"


class AgentLoop:
    def __init__(
        self,
        backend: Any,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = 20,
        tracer: Any = None,  # eval.tracer.Tracer | None
    ):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.tracer = tracer

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]

        total_calls = 0
        high_risk_calls = 0
        consecutive_errors = 0
        step = 0
        # 重复调用检测：跟踪最近 N 次工具调用签名，防止死循环
        _recent_call_sigs: list[str] = []
        _MAX_REPEAT_CALLS = 5

        for turn in range(self.max_turns):
            step += 1
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

            if not tool_calls:
                # 正常退出前重置重复检测
                _recent_call_sigs.clear()
                return assistant.get("content", "")

            # ── 重复调用检测：记录本轮签名 ──
            for call in tool_calls:
                sig = f"{call['name']}:{json.dumps(call.get('arguments', {}), sort_keys=True, ensure_ascii=False)}"
                _recent_call_sigs.append(sig)
            # 只保留最近 _MAX_REPEAT_CALLS 个签名
            if len(_recent_call_sigs) > _MAX_REPEAT_CALLS:
                _recent_call_sigs.pop(0)

            # ── 先执行所有工具（保证对话格式合法）──
            for call in tool_calls:
                # ── 配额检查 ──
                total_calls += 1
                if total_calls > _MAX_TOTAL_CALLS:
                    obs = f"[安全配额] 已达到全局工具调用上限（{_MAX_TOTAL_CALLS} 次），拒绝执行 {call['name']}"
                    messages.append({
                        "role": "tool",
                        "name": call["name"],
                        "tool_call_id": call.get("id"),
                        "content": obs,
                    })
                    continue

                if call["name"] in _HIGH_RISK_TOOLS:
                    high_risk_calls += 1
                    if high_risk_calls > _MAX_HIGH_RISK_CALLS:
                        obs = f"[安全配额] 高风险工具 '{call['name']}' 已达到配额上限（{_MAX_HIGH_RISK_CALLS} 次），拒绝执行"
                        messages.append({
                            "role": "tool",
                            "name": call["name"],
                            "tool_call_id": call.get("id"),
                            "content": obs,
                        })
                        continue

                tool = self.registry.get(call["name"])

                if tool is None:
                    obs = f"错误：未知工具 {call['name']}。可用工具：{', '.join(self.registry.names())}"
                    consecutive_errors += 1
                else:
                    try:
                        obs = tool.run(**call.get("arguments", {}))
                        consecutive_errors = 0  # 成功执行，重置错误计数
                    except Exception as e:
                        hint = _classify_error(e, call["name"])
                        obs = f"工具 {call['name']} 执行出错：{e}\n[修复建议] {hint}"
                        consecutive_errors += 1

                # 连续错误过多时提前终止
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    obs += f"\n\n⚠️ 已连续 {consecutive_errors} 次错误，任务可能无法继续。"

                messages.append({
                    "role": "tool",
                    "name": call["name"],
                    "tool_call_id": call.get("id"),
                    "content": truncate_observation(str(obs)),
                })

            # ── 循环检测（工具执行后）：同一签名连续出现 → 注入提示或终止 ──
            if len(_recent_call_sigs) >= _MAX_REPEAT_CALLS and len(set(_recent_call_sigs)) == 1:
                stuck_call = tool_calls[0]["name"]
                stuck_args = tool_calls[0].get("arguments", {})
                # 检查是否已经注入过提示（防止无限循环）
                _already_warned = any(
                    msg.get("role") == "user" and "[循环检测]" in str(msg.get("content", ""))
                    for msg in messages[-3:]
                )
                if _already_warned:
                    return (
                        f"[检测到模型陷入循环] 连续 {_MAX_REPEAT_CALLS} 次调用 {stuck_call}({json.dumps(stuck_args, ensure_ascii=False)})，"
                        f"注入提示后仍未纠正，自动终止。请检查工具参数是否正确，或简化任务后重试。"
                    )
                # 首次检测：注入提示，给模型一次自我纠正的机会
                hint = (
                    f"⚠️ [循环检测] 你已经连续 {_MAX_REPEAT_CALLS} 次调用了相同的 {stuck_call}({json.dumps(stuck_args, ensure_ascii=False)})。"
                    f"请立即改变策略——如果是要列出待办，改用 task_list(action='list')；"
                    f"如果是要添加待办，请用 task_list(action='add', items=[...]) 并给出具体待办项；"
                    f"如果不需要待办清单，请直接开始执行用户任务，不要再调用 task_list。"
                )
                messages.append({"role": "user", "content": hint})
                _recent_call_sigs.clear()

            # 本轮所有工具执行结束后，再判断是否需要压缩上下文
            messages = maybe_compact(messages, self.backend)

        return "[达到最大轮数上限，未完成任务]"