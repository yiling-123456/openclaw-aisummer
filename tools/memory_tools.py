"""记忆工具（D7/D10）—— 让 Agent 能跨会话记住和召回信息。

将这些工具注册到 ToolRegistry 后，Agent 就能：
  - save_memory: 保存一条记忆（如项目约定、用户偏好）
  - recall_memory: 按关键词召回相关记忆
  - forget_memory: 删除一条记忆
  - list_memories: 列出所有记忆
"""
from __future__ import annotations
from .base import Tool
from agent.memory import AgentMemory

# 模块级单例
_memory: AgentMemory | None = None


def get_memory() -> AgentMemory:
    global _memory
    if _memory is None:
        _memory = AgentMemory()
    return _memory


def _save_memory(key: str, value: str, ttl: int = 604800) -> str:
    """保存一条记忆。ttl 单位为秒，默认 7 天。"""
    return get_memory().save(key, value, ttl)


def _recall_memory(query: str, max_results: int = 5) -> str:
    """按关键词搜索记忆。"""
    results = get_memory().recall(query, max_results)
    if not results:
        return "[记忆] 未找到与查询相关的记忆。"
    return "\n".join(f"- {r}" for r in results)


def _forget_memory(key: str) -> str:
    """删除指定记忆。"""
    return get_memory().forget(key)


def _list_memories() -> str:
    """列出所有已保存的记忆。"""
    keys = get_memory().list_all()
    if not keys:
        return "[记忆] 当前没有已保存的记忆。"
    return "\n".join(f"- {k}" for k in keys)


save_memory_tool = Tool(
    name="save_memory",
    description="保存一条跨会话记忆。下次启动 agent 时可以通过 recall_memory 召回。用于记住用户偏好、项目约定等。",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "记忆的唯一标识/名称"},
            "value": {"type": "string", "description": "记忆的内容"},
            "ttl": {"type": "integer", "description": "有效期（秒），默认 604800（7 天）"},
        },
        "required": ["key", "value"],
    },
    run=_save_memory,
)

recall_memory_tool = Tool(
    name="recall_memory",
    description="按关键词召回之前保存的跨会话记忆。在开始新任务时应该先用此工具检查是否有相关的历史记忆。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 5"},
        },
        "required": ["query"],
    },
    run=_recall_memory,
)

forget_memory_tool = Tool(
    name="forget_memory",
    description="删除一条不再需要的记忆。",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "要删除的记忆标识"},
        },
        "required": ["key"],
    },
    run=_forget_memory,
)

list_memories_tool = Tool(
    name="list_memories",
    description="列出所有已保存的跨会话记忆。",
    parameters={
        "type": "object",
        "properties": {},
    },
    run=_list_memories,
)
