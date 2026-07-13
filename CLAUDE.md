# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

mini-OpenClaw 是一个为期 10 天的课程项目，学生在此骨架基础上逐步构建一个 Claude Code 式的命令行 AI 智能体。整个项目使用 `# TODO[DayN]` 标记指导学生按天完成各模块。里程碑：v1（Day6）端到端可用 → v3（Day9）可扩展 → 终版（Day10）含安全层。

当前已实现（截至 final_v1）：Day1-6 核心功能已完成，Day7-9 大部分完成，Day10 评测框架就绪但任务集待补充。剩余约 32 个 `# TODO[DayN]` 标记分布在各模块。

## 常用命令

```bash
# 环境准备
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# 骨架自检 — 验证所有模块可导入 + FakeBackend 正常
python -m agent.cli --selfcheck

# 运行智能体（需要 DEEPSEEK_API_KEY 环境变量；未配 key 自动回退 FakeBackend）
python -m agent.cli "你的任务描述"

# 连通性验证：调用 DeepSeek API 跑一次简单的 function-calling demo
python demo_m2.py

# 运行评测指标（基于预置样本轨迹）
python -m eval.metrics

# 运行消融实验
python -m eval.ablation

# 查看所有施工点（⚠️ report.md 会命中大量假阳性，建议排除）
grep -rn "TODO\[Day" . --include="*.py"
```

## 核心架构

### 实际数据流（ReAct 主循环）

当前主循环使用 DeepSeek API 的**原生 function-calling**，不依赖手动 prompt 拼接：

```
用户任务 → AgentLoop.run()
  └─ while 未完成（最多 max_turns=20 轮）:
       backend.chat(messages, tools)  →  OpenAI 兼容 API 调用
       │  DeepSeekBackend._to_openai_messages() 转换消息格式
       │  registry.schemas() 提供工具定义（OpenAI tools 格式）
       └─ API 返回 assistant 消息
          ├─ 含 tool_calls → ToolRegistry.get(name).run(**args) → 结果注入 messages
          └─ 无 tool_calls → 返回 content 作为最终答案
```

**重要**：`prompt/render.py` 的 `render_prompt()` 和 `parse_tool_calls()` 是 Day3 的**学习练习**（理解 tokenization 和模板渲染），但**未接入主循环**。主循环通过 `DeepSeekBackend` 直接调用 OpenAI 兼容 API，工具调用解析由 `DeepSeekBackend._normalize()` 完成。

### 模块间接口约定

- **Backend → Loop**：`chat(messages, tools) -> dict` 返回归一化格式 `{"role": "assistant", "content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}`。`DeepSeekBackend` 和 `FakeBackend` 都遵循此接口。`backend/server.py` 已弃用。
- **Tool → Loop**：`Tool` 数据类（name / description / parameters / run）。`run(**arguments) -> str` 返回文本 observation，由主循环以 `role="tool"` 注入消息历史。
- **MCP 集成**：MCP 工具以 `mcp__` 前缀注册到同一个 ToolRegistry，对主循环透明。CLI 启动时自动连接 echo MCP server 和官方 filesystem MCP server（通过 npx）。MCP 客户端通过 stdio + JSON-RPC 与 server 通信。
- **上下文管理**：`agent/context.py` 的 `maybe_compact()` 在超 token 预算时将早期消息摘要为 system 备忘；`truncate_observation()` 截断过长工具结果。`estimate_tokens()` 使用字符数/4 粗估。
- **Skills 格式**：YAML frontmatter（name + description）+ Markdown 正文。`skills/loader.py` 扫描 `*/SKILL.md`，生成可注入系统提示词的能力清单。Skill 不同于 Tool——它是一包领域知识和工作流程，而非单次函数调用。

### 关键设计模式

- **ToolRegistry**：工具按名称注册的字典，提供 `register()`、`get()`、`schemas()`。`build_default_registry()` 是工厂函数，在 `tools/base.py` 中按课程进度逐步取消注释以激活各工具。
- **当前已激活的工具**：read、write、bash、edit、grep、glob、web_fetch、teacher_search、course_search（task_list 仍为 `NotImplementedError`）。
- **系统提示词**：`agent/prompts.py` 包含完整的工作准则、工具说明和正/负面示例。CLI 启动时会将 skill 目录追加入系统提示词。

## 教师评价搜索领域（Day9+ 特色功能）

这是该课程项目的核心领域应用，包含两大工具和安全校验模块：

- **teacher_search**：搜索本地教师评价数据库（CSV 文件），返回指定教师的学生评价原文。每条评价含全局唯一序号 `[#N]`，输出时必须用 `@N+关键词@` 格式引用。
- **course_search**：搜索本地教师 GPA 数据库（gpa.json），根据课程名称查找所有授课教师及其 GPA 量化数据。支持模糊搜索、按评价人数过滤、按 GPA 降序排列对比。
- **safety.py**（`skills/teacher-eval-search/safety.py`）：引用安全校验。后处理检查 `@序号+关键词@` 标签：校验序号是否存在、关键词是否出现在对应原文中、检测缺少引用的评价性语句。由 `verify_citations()` 和 `check_uncited_claims()` 实现。
- **search_engine.py**（`skills/teacher-eval-search/search_engine.py`）：扫描本地 CSV 数据，构建内存索引。单例模式，230k+ 条评论只索引一次。数据目录以 `chalaoshi_csv` 开头自动发现。

## 评测系统

- **eval/tasks.py**：任务定义。`ToolCallCase`（单步工具调用测试用例）和 `E2ETask`（端到端任务）两种粒度。`Task` 类包含程序化成功判据（`check(trajectory) -> bool`）。
- **eval/tracer.py**：轨迹记录器。写 JSONL 日志文件（每步记录 tool_calls、token 计数等），支持 `replay()` 回放。
- **eval/metrics.py**：三项指标——JSON 合法率、工具选择正确率、参数正确率、成功率、平均步数、平均 token 数。
- **eval/judge.py**：LLM-as-judge。按固定 rubric（1-5 分）给一个答复打分。
- **eval/ablation.py**：最小消融实验框架。有/无 system-prompt 两组样本轨迹的成功率对比。

## 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 未配则自动回退 FakeBackend |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名称 | `deepseek-chat` |

## 各模块完成状态与 TODO 分布

| 模块 | 关键交付 | 状态 | 剩余 TODO |
|------|---------|------|-----------|
| `backend/` | DeepSeek API 客户端 + FakeBackend | ✅ 已完成 | 0 |
| `prompt/` | 模板渲染 + 工具调用解析 | ❌ 未实现（学习练习，未接入主循环） | 6 |
| `agent/` | 系统提示词 + ReAct 主循环 + 上下文管理 | ✅ 基础实现完成 | system prompt (2), loop (3), context (2) |
| `tools/` | read/write/bash/edit/grep/glob/web_fetch + teacher_search + course_search | ✅ 9 个工具已实现 | task_list (1), 建议拆文件 (1) |
| `mcp/` | 最小 MCP 客户端 + echo server | ✅ 已实现 | 0（原 4 个 TODO 已补完） |
| `skills/` | Skill 加载器 + teacher-eval-search + csv-quick-report | ✅ 加载器已实现，skill 已存在 | 2（loader 中) |
| `eval/` | 评测 + 消融 | 🟡 框架就绪 | tasks (4), metrics (2) |
| **合计** | | | **~32 个** |

## 添加新工具的流程

1. 在 `tools/` 下新建文件（或追加到 `tools/more_tools.py`），定义 Tool 实例
2. 在 `tools/base.py` 的 `build_default_registry()` 中注册
3. 在 `agent/prompts.py` 的 `SYSTEM_PROMPT` 中添加工具说明
4. 如需 MCP 接入，参考 `mcp/client.py` 的 `register_mcp_tools()`

## 添加新 Skill 的流程

1. 在 `skills/` 下创建新目录，内建 `SKILL.md`
2. SKILL.md 需包含 YAML frontmatter（name + description）+ Markdown 正文
3. CLI 启动时 `skills/loader.py` 会自动扫描并注入系统提示词
