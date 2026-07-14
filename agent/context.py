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
    budget: int = 12000,
) -> list[dict[str, Any]]:
    """超出 token 预算时压缩较早的历史消息。

    预算设为 12000 字符（估计 ~3000 tokens），给教师评价数据留够空间。
    原默认 6000 对于一次搜多门课的场景过于激进，导致早期工具结果
    （教师评价原文）过早被压缩掉，模型在写总结时缺乏引文数据。
    """

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
            "4. 接下来仍需完成的事情。\n"
            "5. 已获取的所有教师姓名与对应的评价数量。\n\n"
            f"对话历史：\n{text}"
        )

        resp = backend.chat(
            [{"role": "user", "content": prompt}],
            tools=[],
        )
        return resp.get("content", "")

    # 2. 永远保留最前面的 system prompt
    system_message = messages[0]

    # 3. 保留最近 8 条消息原文（原为 4），确保多门课程的教师评价数据不易被压缩掉
    keep_recent = 8

    # 按最近 keep_recent 条消息计算切分位置
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


def truncate_observation(text: str, max_chars: int = 28000) -> str:
    """工具结果过长时截断并提示。

    max_chars=28000 比默认的 12000 翻倍，用于应对 teacher_search 返回的大量评价数据。
    教师评价任务一次搜索多位教师可能产生 15000-25000 字符的返回结果，
    截断太狠会导致模型在总结时缺乏原始数据支撑，产生"无数据"幻觉。
    """
    text = _sanitize_surrogates(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，共 {len(text)} 字符，截断至 {max_chars}]"


def _sanitize_surrogates(text: str) -> str:
    """移除无法编码的孤代理字符（surrogate），防止 utf-8 编码崩溃。"""
    # 只对真正包含 surrogates 的字符串做修复（避免不必要的性能开销）
    for ch in text:
        if '\ud800' <= ch <= '\udfff':
            # 存在 surrogate → 用 replace 清理
            return text.encode('utf-8', errors='replace').decode('utf-8')
    return text
