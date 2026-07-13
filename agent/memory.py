"""跨会话记忆模块（Day7/D10）。

提供简单的持久化记忆：
  - 键值对形式存储在 .agent_memory/ 目录下（JSON 文件）
  - 支持 TTL 自动过期
  - 支持模糊召回（子串匹配）
  - 系统提示词注入

用法：
  from agent.memory import AgentMemory
  mem = AgentMemory()
  mem.save("项目约定", "所有测试文件放在 tests/ 目录下")
  ...
  # 下次会话
  mem.recall("测试")  # → ["项目约定: 所有测试文件放在 tests/ 目录下"]
"""

from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any


# 默认存储目录
DEFAULT_MEMORY_DIR = ".agent_memory"
# 默认 TTL（秒）—— 7 天
DEFAULT_TTL = 7 * 24 * 3600
# 单条记忆最大 value 长度
MAX_VALUE_LENGTH = 2000


class AgentMemory:
    """跨会话记忆：JSON 文件持久化，TTL 自动过期。"""

    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or DEFAULT_MEMORY_DIR)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────

    def save(self, key: str, value: str, ttl: int = DEFAULT_TTL) -> str:
        """保存一条记忆。key 为唯一标识，同名覆盖。"""
        if not key or not key.strip():
            return "[记忆] 错误：key 不能为空"
        if len(value) > MAX_VALUE_LENGTH:
            return f"[记忆] 错误：value 过长（{len(value)}/{MAX_VALUE_LENGTH} 字符）"

        safe_name = self._safe_filename(key)
        entry = {
            "key": key.strip(),
            "value": value,
            "created_at": time.time(),
            "expires_at": time.time() + ttl,
        }
        filepath = self.storage_dir / f"{safe_name}.json"
        filepath.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"[记忆] 已保存：{key}"

    def load(self, key: str) -> dict[str, Any] | None:
        """读取单条记忆（精确 key 匹配）。"""
        safe_name = self._safe_filename(key)
        filepath = self.storage_dir / f"{safe_name}.json"
        if not filepath.exists():
            return None
        entry = json.loads(filepath.read_text(encoding="utf-8"))
        if self._is_expired(entry):
            filepath.unlink(missing_ok=True)
            return None
        return entry

    def forget(self, key: str) -> str:
        """删除一条记忆。"""
        safe_name = self._safe_filename(key)
        filepath = self.storage_dir / f"{safe_name}.json"
        if filepath.exists():
            filepath.unlink()
            return f"[记忆] 已删除：{key}"
        return f"[记忆] 未找到：{key}"

    def recall(self, query: str, max_results: int = 5) -> list[str]:
        """模糊召回：在所有记忆中搜索 query（子串匹配 key 和 value）。"""
        self._cleanup_expired()
        results: list[tuple[float, str, str]] = []

        query_lower = query.lower()
        for fpath in self.storage_dir.glob("*.json"):
            try:
                entry = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if self._is_expired(entry):
                fpath.unlink(missing_ok=True)
                continue

            key = entry.get("key", "")
            value = entry.get("value", "")

            # 简单相关性评分：key 完全匹配最高，否则子串匹配
            score = 0.0
            if query_lower == key.lower():
                score = 2.0
            elif query_lower in key.lower():
                score = 1.5
            elif query_lower in value.lower():
                score = 1.0

            if score > 0:
                results.append((score, key, value))

        # 按相关性降序排列
        results.sort(key=lambda r: -r[0])
        return [f"{key}: {value}" for _, key, value in results[:max_results]]

    def list_all(self) -> list[str]:
        """列出所有未过期的记忆 key。"""
        self._cleanup_expired()
        keys = []
        for fpath in self.storage_dir.glob("*.json"):
            try:
                entry = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not self._is_expired(entry):
                keys.append(entry.get("key", fpath.stem))
        return sorted(keys)

    def summarize_for_prompt(self) -> str:
        """生成可注入系统提示词的记忆摘要。"""
        all_keys = self.list_all()
        if not all_keys:
            return ""

        lines = ["\n# 跨会话记忆（从之前的会话中记住的信息）"]
        for key in all_keys[:20]:  # 最多 20 条
            entry = self.load(key)
            if entry:
                # 截断过长的 value
                v = entry["value"]
                if len(v) > 150:
                    v = v[:150] + "..."
                lines.append(f"- {key}: {v}")
        return "\n".join(lines)

    # ── 内部 ──────────────────────────────────────────────────────

    @staticmethod
    def _safe_filename(key: str) -> str:
        """将 key 转为安全的文件名。"""
        safe = "".join(
            c if c.isalnum() or c in "._- " else "_"
            for c in key.strip()
        )
        return safe.strip() or "unnamed"

    @staticmethod
    def _is_expired(entry: dict) -> bool:
        return time.time() > entry.get("expires_at", 0)

    def _cleanup_expired(self) -> int:
        """清理过期记忆，返回清理数量。"""
        cleaned = 0
        for fpath in self.storage_dir.glob("*.json"):
            try:
                entry = json.loads(fpath.read_text(encoding="utf-8"))
                if self._is_expired(entry):
                    fpath.unlink(missing_ok=True)
                    cleaned += 1
            except (json.JSONDecodeError, OSError):
                # 损坏的文件直接删除
                fpath.unlink(missing_ok=True)
                cleaned += 1
        return cleaned
