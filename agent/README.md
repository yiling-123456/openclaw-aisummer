# agent/ — Agent 核心层

## 架构

```
CLI (cli.py)
  └─ AgentLoop (loop.py)
       ├─ Backend (backend/client.py)
       ├─ ToolRegistry (tools/base.py)
       ├─ Tracer (eval/tracer.py)
       ├─ Context (context.py)
       ├─ Memory (memory.py)
       └─ Prompts (prompts.py)
```

## 模块说明

### loop.py — ReAct 主循环

核心循环：`while 未完成 → backend.chat() → 执行 tool_calls → 注入 observation`

**设计决策**：
- 使用原生 OpenAI function-calling，而非手动 `<tool_call>` 标签解析。这比手动 prompt 拼接更可靠——解析由 API 保证。
- `max_turns=20` 防止无限循环。
- 安全配额：全局 100 次工具调用 + 高风险工具 30 次限制。
- `tracer` 参数可选，连接到 eval/tracer.py 进行轨迹记录。

### context.py — 上下文管理

**Compaction 策略**：
- 当 `estimate_tokens(messages) > 6000` 时触发。
- 保留 system prompt + 最近 4 条消息原文。
- 较早消息调用模型压缩为"历史备忘"摘要。
- `truncate_observation()` 截断过长的工具结果（12000 字符）。

**为什么选 6000 budget**：DeepSeek 上下文 64K，6000 是保守值，给工具输出和后续对话留足空间。

### memory.py — 跨会话记忆（D7/D10）

- JSON 文件持久化在 `.agent_memory/` 目录。
- TTL 自动过期（默认 7 天）。
- 模糊召回：子串匹配 key + value。
- 自动注入系统提示词。

**为什么不使用 SQLite**：JSON 文件方案足够轻量，且对 230k 条评价的教师数据场景不构成性能瓶颈。跨平台兼容性好。

### prompts.py — 系统提示词

静态系统提示词 + 动态部分（Skills 目录 + Memory 摘要）。详见文件内注释。

## 安全层

- **路径沙箱**：所有文件操作经过路径校验（tools/fs.py `_resolve_safe`）
- **命令拦截**：危险 shell 命令自动拒绝（tools/shell.py）
- **SSRF 防护**：web_fetch 阻止内网/保留 IP（tools/more_tools.py）
- **工具配额**：loop.py 限制高风险工具调用次数
- **引用安全**：teacher-eval-search Skill 的引用校验后处理（safety.py）

## 可观测性

- 每次运行自动生成 JSONL 轨迹文件到 `.agent_traces/`
- 轨迹包含每步的 tool_calls、token 用量和时间戳
- 可通过 `eval/tracer.py` 的 `replay()` 回放
