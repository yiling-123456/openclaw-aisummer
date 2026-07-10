"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)
              messages.append(tool_result(obs))
      else:
          return assistant.content
"""

from __future__ import annotations
from typing import Any

from tools.base import ToolRegistry
from agent.context import maybe_compact, truncate_observation


class AgentLoop:
    def __init__(
        self,
        backend: Any,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = 20,
    ):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]

        for turn in range(self.max_turns):
            assistant = self.backend.chat(
                messages,
                tools=self.registry.schemas(),
            )

            messages.append({
                "role": "assistant",
                "content": assistant.get("content") or "",
                "tool_calls": assistant.get("tool_calls") or [],
            })

            tool_calls = assistant.get("tool_calls") or []

            if not tool_calls:
                return assistant.get("content", "")

            for call in tool_calls:
                tool = self.registry.get(call["name"])

                if tool is None:
                    obs = f"错误：未知工具 {call['name']}"
                else:
                    try:
                        obs = tool.run(**call.get("arguments", {}))
                    except Exception as e:
                        obs = f"工具 {call['name']} 执行出错：{e}"

                messages.append({
                    "role": "tool",
                    "name": call["name"],
                    "tool_call_id": call.get("id"),
                    "content": truncate_observation(str(obs)),
                })

            # 本轮所有工具执行结束后，再判断是否需要压缩上下文
            messages = maybe_compact(messages, self.backend)

        return "[达到最大轮数上限，未完成任务]"