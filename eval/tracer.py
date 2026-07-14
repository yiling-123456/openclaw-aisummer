"""极小轨迹记录器：一步一行 JSON（JSONL），可回放。含 token 成本核算。"""
from __future__ import annotations
import json, time
from pathlib import Path

# ── DeepSeek 计价（每 1M token，美元） ──
DEEPSEEK_INPUT_COST_PER_M = 0.14    # $0.14 / 1M input tokens
DEEPSEEK_OUTPUT_COST_PER_M = 0.28   # $0.28 / 1M output tokens


def compute_cost(prompt_tokens: int, completion_tokens: int,
                 model: str = "deepseek-chat") -> dict:
    """计算 token 成本（美元）。

    Args:
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        model: 模型名称（预留扩展）

    Returns:
        {"prompt_cost": float, "completion_cost": float, "total_cost": float}
    """
    input_cost = prompt_tokens / 1_000_000 * DEEPSEEK_INPUT_COST_PER_M
    output_cost = completion_tokens / 1_000_000 * DEEPSEEK_OUTPUT_COST_PER_M
    return {
        "prompt_cost": round(input_cost, 6),
        "completion_cost": round(output_cost, 6),
        "total_cost": round(input_cost + output_cost, 6),
    }


class Tracer:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.write_text("", encoding="utf-8")   # 清空/新建
        # 运行中累计
        self._total_prompt: int = 0
        self._total_completion: int = 0

    def log_step(self, step: int, tool_calls: list, prompt_tokens: int,
                 completion_tokens: int, note: str = "") -> None:
        self._total_prompt += prompt_tokens
        self._total_completion += completion_tokens
        cost = compute_cost(prompt_tokens, completion_tokens)
        event = {"ts": round(time.time(), 3), "step": step,
                 "tool_calls": tool_calls, "note": note,
                 "prompt_tokens": prompt_tokens,
                 "completion_tokens": completion_tokens,
                 "cost": cost["total_cost"]}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def cost_summary(self) -> str:
        """返回本次会话的成本摘要。"""
        cost = compute_cost(self._total_prompt, self._total_completion)
        return (
            f"[成本] 本次会话 token 用量：\n"
            f"  Input tokens:  {self._total_prompt:,}\n"
            f"  Output tokens: {self._total_completion:,}\n"
            f"  Total tokens:  {self._total_prompt + self._total_completion:,}\n"
            f"  估算成本:      ${cost['total_cost']:.4f} "
            f"(input ${cost['prompt_cost']:.4f} + output ${cost['completion_cost']:.4f})"
        )

def replay(path: str) -> None:
    """把一条 JSONL 轨迹逐步打印出来（回放），含成本统计。"""
    total_tok = 0
    total_prompt = 0
    total_completion = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        tok = e["prompt_tokens"] + e["completion_tokens"]
        total_tok += tok
        total_prompt += e["prompt_tokens"]
        total_completion += e["completion_tokens"]
        names = [tc["name"] for tc in e["tool_calls"]] or ["(无工具调用)"]
        print(f"  step {e['step']}: 调用 {names}  | 本步 {tok} tok  | {e['note']}")
    cost = compute_cost(total_prompt, total_completion)
    print(f"  —— 轨迹共 {total_tok} token，估算成本 ${cost['total_cost']:.4f}")

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