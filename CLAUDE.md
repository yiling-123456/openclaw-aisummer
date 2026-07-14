# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

mini-OpenClaw 是一个 10 天课程项目，学生基于此骨架逐步构建一个 Claude Code 式的命令行 AI 智能体。核心是一个 **ReAct 主循环**不断调用 **DeepSeek API**（原生 function-calling），模型输出工具调用后由主循环执行并把结果喂回模型，直到任务完成。

**当前状态**：Day1-6 核心功能已完成，Day7-9 大部分完成，Day10 评测框架就绪但任务集待补充。约 32 个 `# TODO[DayN]` 标记分布在代码中。

**里程碑**：v1（Day6）端到端可用 → v3（Day9）可扩展 → 终版（Day10）含安全层。

## 常用命令

```bash
# 环境准备
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# 骨架自检 — 验证模块可导入 + FakeBackend 正常
python -m agent.cli --selfcheck

# 运行 agent（REPL 模式，无参数时自动进入交互式终端）
python -m agent.cli
python -m agent.cli "创建 hello.py 并运行"

# 查看完整模型输出（含引用验证标记）
python -m agent.cli "介绍张老师" -a

# 连通性验证（需 DEEPSEEK_API_KEY）
python demo_m2.py

# 评测指标（基于预置样本轨迹）
python -m eval.metrics

# 消融实验（A-E，模拟轨迹数据）
python -m eval.ablation

# 查找所有 TODO 施工点
grep -rn "TODO\[Day" --include="*.py" .
```

## 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 未配则自动回退 FakeBackend |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名称 | `deepseek-chat` |

## 核心架构

### 数据流（ReAct 主循环）

使用 DeepSeek API 的**原生 function-calling**，不依赖手动 prompt 拼接：

```
用户任务 → AgentLoop.run()
  ├─ 规划状态注入（每轮将 todo 进度拼进上下文）
  ├─ 连续错误时注入反思提示（有上限，防无限套娃）
  ├─ 无进展预警（连续 N 步无进度 → 提醒模型）
  └─ while 未完成（最多 40 轮）:
       backend.chat(messages, tools)  →  OpenAI 兼容 API
       │  registry.schemas() 提供工具定义（OpenAI tools 格式）
       └─ API 返回 assistant 消息
          ├─ 含 tool_calls → 瞬时错误自动重试退避 → 结果注入 messages
          └─ 无 tool_calls → planner.all_done() 检查 → 通过则返回，否则提示继续
```

**关键**：`prompt/render.py` 的 `render_prompt()` 和 `parse_tool_calls()` 是 Day3 学习练习（未接入主循环）。主循环通过 `DeepSeekBackend` 直接调 OpenAI 兼容 API。

### Backend → Loop 接口约定

```python
# chat() 返回归一化格式：
{"role": "assistant", "content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
```
`DeepSeekBackend`（真实 API，`backend/client.py`）和 `FakeBackend`（离线开发，`backend/fake_backend.py`）都遵循此接口。

### 工具注册模式（`tools/base.py`）

每个工具是 `Tool(name, description, parameters, run)` 对象：
- `parameters`：JSON Schema（OpenAI tools 格式，给模型的函数定义）
- `run(**arguments) -> str`：执行函数，返回观察文本
- `ToolRegistry`：按名称注册的字典，`schemas()` 输出给 API，`get()` 在运行时查找

`build_default_registry()` 是工厂函数，按 Day 逐步激活工具。当前已激活所有工具（read/write/bash/edit/grep/glob/web_fetch/task_list + todo_write/todo_update + teacher_search/course_search + 4个 memory 工具）。

### MCP 集成（Day8）

MCP 工具以 `mcp__` 前缀注册到 ToolRegistry，对主循环透明。CLI 启动时自动连接 echo server + filesystem MCP server。客户端通过 stdio + JSON-RPC 通信（`mcp/client.py`），内置超时保护（30s）和大响应保护（5MB）。

### SKILL 系统（Day9）

`skills/loader.py` 扫描 `*/SKILL.md`，解析 YAML frontmatter + Markdown 正文，注入系统提示词。当前有两个 skill：
- `example-skill`：CSV 报告示例
- `teacher-eval-search`：教师评价检索，含引用注入与安全校验流程

### 教师评价搜索领域

这是课程核心领域应用，涉及多个模块的协作：
- `tools/teacher_search.py`：搜索本地教师评价数据库（CSV）
- `tools/course_search.py`：按课程名查授课教师（基于 gpa.json）
- `skills/teacher-eval-search/search_engine.py`：CSV 数据索引，单例模式，230k+ 评论只索引一次
- `skills/teacher-eval-search/safety.py`：引用后处理，校验 `@序号+关键词@` 标签的真实性
- `skills/teacher-eval-search/SKILL.md`：完整领域工作流（意图识别 → 检索 → 归纳 → 引用注入 → 安全校验）

### 规划层（`agent/planning.py`）— Day9+

为 ReAct 主循环添加审议式规划能力。核心是 `PlanningManager` 类，管理待办项的完整生命周期状态机：

```
pending → in_progress → completed
                        → blocked → pending（解封重试）
                        → cancelled
```

| 特性 | 机制 |
|------|------|
| 状态注入 | 每轮 loop 将 todo 进度以 system message 拼入上下文 |
| 反思追踪 | 同一子任务最多反思 N 次（默认 3），超限自动标记 blocked |
| 错误恢复 | 瞬时错误（timeout/network）自动指数退避重试 3 次；非瞬时错误计入 error_count，超限标记 blocked |
| 无进展检测 | 连续 5 步无 todo 状态变化 → 预警提示模型 |
| 有界停止 | 40 步硬上限；all_done() 判据确保不会"没做完就声称完成" |
| todo 工具 | `todo_write` 分解任务、`todo_update` 标记进度，是系统提示词中的"首选长任务工作流" |

映射关系：`todo_write` / `todo_update`（Claude Code 的 TodoWrite/TodoUpdate）→ `PlanningManager` ← `task_list`（委派至同一后端）。

`maybe_compact()` 在超 token 预算（6000 chars 粗估）时将早期消息摘要为 system 备忘，保留 system prompt + 最近 4 条原文。`truncate_observation()` 截断超长工具结果（12000 chars）。

### 跨会话记忆（`agent/memory.py`）

JSON 文件持久化到 `.agent_memory/` 目录，支持 TTL 过期（默认 7 天）、模糊召回（子串匹配 key/value）、自动注入系统提示词。Agent 可通过 `save_memory` / `recall_memory` / `forget_memory` / `list_memories` 四个工具直接操作。

### 安全/配额层

安全防护散布在多个模块中：

| 模块 | 防护 |
|------|------|
| `agent/loop.py` | 全局配额（100次）、高风险工具配额（30次）、连续错误检测（3次）、循环调用检测（5次相同）、规划层反射上限、瞬时错误自动重试、完成判据预检 |
| `tools/shell.py` | 危险命令模式拦截（rm -rf/sudo/curl-to-bash/fork bomb 等） |
| `tools/fs.py` | 路径遍历防护、敏感文件拦截（.env/密钥/.git） |
| `tools/more_tools.py` | SSRF 防护（内网 IP/127.0.0.1/非标准端口拦截） |
| `backend/client.py` | API 错误响应截断（前500字符，防敏感信息泄露） |
| `skills/teacher-eval-search/safety.py` | 引用真实性校验（序号存在性 + 关键词匹配 + 无引用声明检测） |

### 评测系统（`eval/`）

| 模块 | 功能 |
|------|------|
| `eval/tracer.py` | 轨迹记录器，JSONL 格式记录每步 tool_calls + token 用量 |
| `eval/metrics.py` | 三项指标：JSON 合法率 / 工具选择正确率 / 参数正确率 |
| `eval/judge.py` | LLM-as-judge，按 rubric 给答复打 1-5 分 |
| `eval/tasks.py` | 评测任务定义（Task + E2ETask）+ 成功判据 |
| `eval/ablation.py` | 5 组消融实验（A:system-prompt / B:compaction / C:task_list / D:error-recovery / E:memory） |

### 目录结构

```
├── agent/               # 主循环 + 系统提示词 + 上下文管理 + 记忆 + 规划层
│   ├── cli.py           # 入口（--selfcheck / 单次执行 / REPL）
│   ├── loop.py          # ReAct 主循环（配额+循环检测+错误分类+规划注入+反思）
│   ├── prompts.py       # 系统提示词
│   ├── context.py       # 上下文压缩/截断
│   ├── memory.py        # 跨会话记忆
│   └── planning.py      # Day9+: TodoList 状态机 + 反思追踪 + 进度管理
├── backend/
│   ├── client.py        # DeepSeek API 客户端
│   └── fake_backend.py  # 离线占位后端（无 key 时自动回退）
├── tools/               # 工具实现（每个 .py 一个或多个工具）
│   ├── base.py          # Tool / ToolRegistry / build_default_registry
│   ├── fs.py            # read / write（路径安全+敏感文件拦截）
│   ├── shell.py         # bash（危险命令拦截）
│   ├── more_tools.py    # edit / grep / glob / web_fetch / task_list（委派至 PlanningManager）
│   ├── teacher_search.py
│   ├── course_search.py
│   ├── todo_tools.py    # Day9+: todo_write / todo_update（规划层工具）
│   └── memory_tools.py  # save/recall/forget/list_memories
├── mcp/                 # MCP 客户端 + echo server
│   ├── client.py        # 最小 MCP 客户端（stdio + JSON-RPC）
│   └── echo_server.py   # 测试用 echo MCP server
├── skills/              # Skill 加载器 + 预置 skill
│   ├── loader.py        # 扫描 */SKILL.md
│   ├── example-skill/
│   └── teacher-eval-search/
│       ├── SKILL.md     # 教师评价领域工作流
│       ├── search_engine.py  # CSV 数据索引（230k+ 评论）
│       └── safety.py    # 引用校验后处理
├── eval/                # 评测框架
│   ├── metrics.py       # JSON 合法率/工具正确率/参数正确率
│   ├── judge.py         # LLM-as-judge 打分
│   ├── tasks.py         # 任务定义 + 成功判据
│   ├── tracer.py        # JSONL 轨迹记录
│   └── ablation.py      # 5 组消融实验
└── prompt/              # Day3 学习练习（未接入主循环）
    └── render.py        # render_prompt() + parse_tool_calls()
```
