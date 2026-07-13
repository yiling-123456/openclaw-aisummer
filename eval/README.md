# eval/ — 评测系统（Day7/Day10）

## 评测体系

```
eval/
├── tasks.py       # 任务集 + 判据
├── metrics.py     # 三项指标（JSON 合法/工具选择/参数正确）
├── tracer.py      # 轨迹记录器（JSONL）
├── judge.py       # LLM-as-judge 评分（1-5）
└── ablation.py    # 消融实验
```

## 三类评测

### A) 工具调用质量（metrics.py）

在固定测试集 `TOOLCALL_TESTSET` 上计算：
- **JSON 合法率**：模型输出的 tool_call 格式是否正确
- **工具选择正确率**：是否调用了正确的工具
- **参数正确率**：调用参数是否与期望一致

### B) 端到端任务成功率（tasks.py）

`E2E_TASKS`：6 条端到端任务，覆盖：
- 基础文件操作
- 多步代码扫描
- 教师评价多工具协同
- 跨会话记忆
- 错误恢复
- 安全拦截

### C) 消融实验（ablation.py）

对比不同配置下的任务成功率：
- 有/无 system prompt
- 有/无 compaction
- 有/无 task_list 规划
- 有/无 错误恢复
- 有/无 跨会话记忆

## 可观测性（D9）

### Tracer（tracer.py）

JSONL 格式，每步一行：
```json
{"ts": 1710000000.0, "step": 1, "tool_calls": [...],
 "prompt_tokens": 310, "completion_tokens": 22, "note": "read; grep"}
```

- CLI 每次运行自动生成轨迹（`.agent_traces/trace_*.jsonl`）
- `replay()` 可回放并统计 token 用量
- 用于答辩时"用 trace 佐证设计"

### LLM Judge（judge.py）

用 DeepSeek 作为评审，按 1-5 分 rubric 给答复打分。模板化 rubric 确保一致性。

## 运行

```bash
python -m eval.metrics    # 基础指标
python -m eval.ablation   # 消融实验
python -m eval.tracer     # 轨迹回放
```
