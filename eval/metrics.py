"""工具调用三项指标（Day6 Lab3 验收用）。

  - JSON 合法率：模型输出的 tool_call 能否被 json.loads 成功解析。
  - 工具选择正确率：选对工具名的比例。
  - 参数正确率：关键参数与期望一致的比例。
"""
from __future__ import annotations
import json
from typing import Any


def json_valid_rate(raw_outputs: list[str]) -> float:
    """raw_outputs：模型为每条用例生成的 <tool_call>{...}</tool_call> 原文。"""
    ok = 0
    for out in raw_outputs:
        # TODO[Day7] 抽出 {...} 部分尝试 json.loads（可复用 prompt.render.parse_tool_calls）
        try:
            json.loads(_extract_json(out)); ok += 1
        except Exception:  # noqa
            pass
    return ok / max(len(raw_outputs), 1)


def tool_choice_accuracy(preds: list[dict], expected_tools: list[str]) -> float:
    correct = sum(1 for p, e in zip(preds, expected_tools) if p.get("name") == e)
    return correct / max(len(expected_tools), 1)


def arg_accuracy(preds: list[dict], expected_args: list[dict]) -> float:
    """关键参数匹配率：期望 args 的每个键值都在预测里对上。"""
    correct = 0
    for p, e in zip(preds, expected_args):
        pa = p.get("arguments", {})
        if all(str(pa.get(k)) == str(v) for k, v in e.items()):
            correct += 1
    return correct / max(len(expected_args), 1)


def _extract_json(text: str) -> str:
    # TODO[Day7] 从 <tool_call>...</tool_call> 中取出 JSON 串
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 else "{}"


#from __future__ import annotations
import json, re
from typing import Any

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

# 一条记录 = 一次任务运行留下的轨迹。steps 里每步含：模型这步请求的
# tool_calls、原始文本 raw（含 <tool_call>）、以及该步的 token 计数。
SAMPLE_RECORDS: list[dict[str, Any]] = [
    {"task": "read-config",
     "steps": [
         {"tool_calls": [{"name": "read", "arguments": {"path": "config.json"}}],
          "raw": '<tool_call>{"name":"read","arguments":{"path":"config.json"}}</tool_call>',
          "prompt_tokens": 310, "completion_tokens": 22},
     ],
     "final": "config.json 里 timeout = 30 秒。"},
    {"task": "list-dir",
     "steps": [
         {"tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}],
          "raw": '<tool_call>{"name":"bash","arguments":{"command":"ls"}}</tool_call>',
          "prompt_tokens": 290, "completion_tokens": 18},
     ],
     "final": "当前目录有：main.py config.json README.md"},
    {"task": "read-config",          # 一条“失败/低质量”样本：JSON 被截断，且没报出值
     "steps": [
         {"tool_calls": [],
          "raw": '<tool_call>{"name":"read","arguments":{"path":',   # 坏 JSON
          "prompt_tokens": 305, "completion_tokens": 12},
         {"tool_calls": [], "raw": "我不确定 timeout 的值。",
          "prompt_tokens": 340, "completion_tokens": 15},
     ],
     "final": "我不确定 timeout 的值。"},
]

def success_rate(tasks: list, records: list[dict]) -> float:
    """对每条 (task, trajectory) 记录跑 task.check，返回成功比例。"""
    by_name = {t.name: t for t in tasks}
    ok = 0
    for r in records:
        task = by_name.get(r["task"])
        if task and task.check(r):      # 复用步骤 1 的成功判据
            ok += 1
    return ok / max(len(records), 1)

def step_count(record: dict) -> int:
    return len(record["steps"])

def token_count(record: dict) -> int:
    return sum(s.get("prompt_tokens", 0) + s.get("completion_tokens", 0)
               for s in record["steps"])

def json_valid_rate(records: list[dict]) -> float:
    """从每步的 tool_calls（结构化）或 raw 里的 <tool_call>（文本）提取 JSON 并校验。"""
    total, ok = 0, 0
    for r in records:
        for s in r["steps"]:
            m = TOOL_CALL_RE.search(s.get("raw", ""))
            if not m:
                continue                # 这步没打算调工具，不计入
            total += 1
            try:
                json.loads(m.group(1)); ok += 1
            except json.JSONDecodeError:
                pass                     # 坏 JSON：计入分母、不计入分子
    return ok / max(total, 1)

if __name__ == "__main__":
    from eval.tasks import SAMPLE_TASKS
    recs = SAMPLE_RECORDS
    print("成功率        :", success_rate(SAMPLE_TASKS, recs))
    print("平均步数      :", sum(step_count(r) for r in recs) / len(recs))
    print("平均 token    :", sum(token_count(r) for r in recs) / len(recs))
    print("JSON 合法率   :", json_valid_rate(recs))