"""极小轨迹记录器：一步一行 JSON（JSONL），可回放。"""
from __future__ import annotations
import json, time
from pathlib import Path

class Tracer:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.write_text("", encoding="utf-8")   # 清空/新建

    def log_step(self, step: int, tool_calls: list, prompt_tokens: int,
                 completion_tokens: int, note: str = "") -> None:
        event = {"ts": round(time.time(), 3), "step": step,
                 "tool_calls": tool_calls, "note": note,
                 "prompt_tokens": prompt_tokens,
                 "completion_tokens": completion_tokens}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

def replay(path: str) -> None:
    """把一条 JSONL 轨迹逐步打印出来（回放）。"""
    total_tok = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        tok = e["prompt_tokens"] + e["completion_tokens"]
        total_tok += tok
        names = [tc["name"] for tc in e["tool_calls"]] or ["(无工具调用)"]
        print(f"  step {e['step']}: 调用 {names}  | 本步 {tok} tok  | {e['note']}")
    print(f"  —— 轨迹共 {total_tok} token")

if __name__ == "__main__":
    # 用步骤 2 的一条样本喂进来（模拟 D4 真 agent 逐步 log 的效果）
    from eval.metrics import SAMPLE_RECORDS
    rec = SAMPLE_RECORDS[0]
    tr = Tracer("eval/trace_sample.jsonl")
    for i, s in enumerate(rec["steps"]):
        tr.log_step(i, s.get("tool_calls", []),
                    s.get("prompt_tokens", 0), s.get("completion_tokens", 0),
                    note=s.get("raw", "")[:40])
    print(f"已写入 eval/trace_sample.jsonl（任务={rec['task']}）；回放：")
    replay("eval/trace_sample.jsonl")