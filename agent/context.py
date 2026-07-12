"""上下文管理（Day7）：token 预算、滑动窗口、自动摘要 / compaction。

模型上下文窗口有限。长任务里 messages 会越堆越长，迟早超预算。
策略：
  - 估算当前 messages 的 token 数；
  - 超过阈值时触发 compaction：把较早的对话摘要成一条 system 备忘，
    保留最近 K 轮原文 + 关键工具结果；
  - tool result 过长时先截断/摘要再注入。
"""
from __future__ import annotations
from typing import Any


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    # TODO[Day7] 粗估即可（字符数/4 或用 tokenizer 精确数）
    return sum(len(str(m.get("content", ""))) for m in messages) // 4

def maybe_compact(
    messages: list[dict[str, Any]],
    backend: Any,
    budget: int = 6000,
) -> list[dict[str, Any]]:
    """超出 token 预算时压缩较早的历史消息。"""

    # 1. 没有超过预算，原样返回
    if estimate_tokens(messages) <= budget:
        return messages

    # 至少要保留 system 和最近消息
    if len(messages) <= 2:
        return messages

    def _summarize(backend: Any, chunk: list[dict[str, Any]]) -> str:
        """调用模型，把较早的对话历史压缩成摘要。"""
        text = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}"
            for m in chunk
        )

        prompt = (
            "把下面的对话历史压缩成简洁的要点，必须保留：\n"
            "1. 用户的任务目标；\n"
            "2. 已经完成的步骤；\n"
            "3. 已发现的关键信息、文件和错误；\n"
            "4. 接下来仍需完成的事情。\n\n"
            f"对话历史：\n{text}"
        )

        resp = backend.chat(
            [{"role": "user", "content": prompt}],
            tools=[],
        )
        return resp.get("content", "")

    # 2. 永远保留最前面的 system prompt
    system_message = messages[0]

    # 3. 保留最近 4 条消息原文
    keep_recent = 4

    # 先按最近 4 条消息计算切分位置
    split_index = max(1, len(messages) - keep_recent)

    # 不能让 recent_messages 从 tool 消息开始。
    # 如果切到了工具结果中间，就向前移动，直到包含对应的 assistant(tool_calls)。
    while (
        split_index > 1
        and messages[split_index].get("role") == "tool"
    ):
        split_index -= 1

    old_messages = messages[1:split_index]
    recent_messages = messages[split_index:]

    # 没有可以压缩的旧消息时，直接返回
    if not old_messages:
        return messages

    # 4. 把较早的消息压缩成一条 system 备忘
    summary = _summarize(backend, old_messages)

    memo_message = {
        "role": "system",
        "content": f"历史备忘：\n{summary}",
    }

    # 5. 返回：原 system + 历史摘要 + 最近消息
    return [
        system_message,
        memo_message,
        *recent_messages,
    ]


def truncate_observation(text: str, max_chars: int = 4000) -> str:
    """工具结果过长时截断并提示。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，共 {len(text)} 字符]"
