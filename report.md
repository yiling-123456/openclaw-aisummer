# TODO 注释汇总报告

> 生成时间：自动扫描  
> 扫描范围：本项目所有 `.py` 文件

---

## mcp/client.py

| 行号 | 内容 |
|------|------|
| 28 | `# TODO[Day8] 启动子进程，stdin/stdout 接管，做 initialize 握手` |
| 32 | `# TODO[Day8] 发一条 JSON-RPC 请求（带自增 id），读回对应响应` |
| 36 | `# TODO[Day8] 调 tools/list，返回工具描述列表` |
| 40 | `# TODO[Day8] 调 tools/call，返回结果文本` |

## prompt/render.py

| 行号 | 内容 |
|------|------|
| 18 | `# TODO[Day3] 校对你所用模型的真实特殊标记！拼错一个 token，模型行为就会跑偏。` |
| 29 | `# TODO[Day3] 设计一个清晰的工具说明格式，并约定模型用` |
| 46 | `# TODO[Day3] 把 tools 说明并入 system 段` |
| 47 | `# TODO[Day3] 逐条 message 用 ROLE_TOKENS 包裹拼接` |
| 48 | `# TODO[Day3] 末尾以 assistant 起始标记结尾，提示模型开始生成` |
| 54 | `# TODO[Day3] 用正则/状态机提取所有 <tool_call>...</tool_call>，json.loads 出 name/arguments` |

## eval/metrics.py

| 行号 | 内容 |
|------|------|
| 16 | `# TODO[Day7] 抽出 {...} 部分尝试 json.loads（可复用 prompt.render.parse_tool_calls）` |
| 40 | `# TODO[Day7] 从 <tool_call>...</tool_call> 中取出 JSON 串` |

## eval/tasks.py

| 行号 | 内容 |
|------|------|
| 37 | `# TODO[Day3] 再补一条"你组领域"的任务判据（下面 _check_domain）` |
| 65 | `# TODO[Day7] 按你组的领域补充更多用例` |
| 79 | `E2ETask("todo-report", "扫描本项目所有 Python 文件里的 TODO 注释，生成 markdown 报告",` |
| 80 | `"生成的报告列出了真实存在的 TODO"),` |
| 81 | `# TODO[Day10] 补充你领域的任务` |

## agent/prompts.py

| 行号 | 内容 |
|------|------|
| 60 | `# TODO[Day5] 在此补充：工具列表说明、正/负面示例、领域相关的行为约束。` |
| 61 | `# TODO[Day7] 长任务时引导模型使用 task_list 维护待办。` |

## agent/cli.py

| 行号 | 内容 |
|------|------|
| 38 | `print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")` |

## agent/context.py

| 行号 | 内容 |
|------|------|
| 15 | `# TODO[Day7] 粗估即可（字符数/4 或用 tokenizer 精确数）` |
| 23 | `# TODO[Day7] 实现 compaction：` |

## agent/loop.py

| 行号 | 内容 |
|------|------|
| 43 | `# TODO[Day5] 分发并执行工具，把每个结果作为 role="tool" 注入 messages：` |
| 49 | `# TODO[Day7] 加错误恢复（try/except，把异常文本作为 observation，让模型自我修复）` |
| 54 | `# TODO[Day7] 在这里做上下文管理：超出 token 预算时触发 compaction（见 agent/context.py）` |

## skills/loader.py

| 行号 | 内容 |
|------|------|
| 32 | `# TODO[Day9] 解析 YAML frontmatter（name/description）+ 正文 body` |
| 46 | `# TODO[Day9] 渲染成一段文本，放进系统提示词` |

## tools/base.py

| 行号 | 内容 |
|------|------|
| 62 | `# TODO[Day5] 取消注释并实现：` |
| 68 | `# TODO[Day6] 再加入完整工具集（→ v1 里程碑）：` |
| 73 | `# TODO[Day7] 再加入：` |

## tools/more_tools.py

| 行号 | 内容 |
|------|------|
| 3 | `每个工具上午讲设计权衡，下午实现。这里只给签名与 TODO，便于你拆到独立文件。` |
| 56 | `# TODO[Day7] httpx 抓取 -> markdownify 转 markdown -> 截断到预算内` |
| 62 | `# TODO[Day7] 维护一个结构化待办（add/update/complete），作为模型的 scratchpad` |

---

## 统计

| 文件 | TODO 数量 |
|------|-----------|
| mcp/client.py | 4 |
| prompt/render.py | 6 |
| eval/metrics.py | 2 |
| eval/tasks.py | 4 |
| agent/prompts.py | 2 |
| agent/cli.py | 1 |
| agent/context.py | 2 |
| agent/loop.py | 3 |
| skills/loader.py | 2 |
| tools/base.py | 3 |
| tools/more_tools.py | 3 |
| **合计** | **32** |
