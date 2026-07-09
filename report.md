# TODO 注释汇总

| 文件 | 行号 | 内容 |
|------|------|------|
| eval/metrics.py | 76 | `# TODO[Day7] 抽出 {...} 部分尝试 json.loads（可复用 prompt.render.parse_tool_calls）` |
| eval/metrics.py | 100 | `# TODO[Day7] 从 <tool_call>...</tool_call> 中取出 JSON 串` |
| eval/tasks.py | 31 | `# TODO[Day3] 再补一条"你组领域"的任务判据（下面 _check_domain）` |
| agent/loop.py | 43 | `# TODO[Day5] 分发并执行工具，把每个结果作为 role="tool" 注入 messages：` |
| agent/loop.py | 49 | `# TODO[Day7] 加错误恢复（try/except，把异常文本作为 observation，让模型自我修复）` |
| agent/loop.py | 54 | `# TODO[Day7] 在这里做上下文管理：超出 token 预算时触发 compaction（见 agent/context.py）` |
| agent/cli.py | 38 | `print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")` |
| agent/context.py | 15 | `# TODO[Day7] 粗估即可（字符数/4 或用 tokenizer 精确数）` |
| agent/context.py | 23 | `# TODO[Day7] 实现 compaction：` |
| agent/prompts.py | 18 | `# TODO[Day5] 在此补充：工具列表说明、正/负面示例、领域相关的行为约束。` |
| agent/prompts.py | 19 | `# TODO[Day7] 长任务时引导模型使用 task_list 维护待办。` |
| mcp/client.py | 28 | `# TODO[Day8] 启动子进程，stdin/stdout 接管，做 initialize 握手` |
| mcp/client.py | 32 | `# TODO[Day8] 发一条 JSON-RPC 请求（带自增 id），读回对应响应` |
| mcp/client.py | 36 | `# TODO[Day8] 调 tools/list，返回工具描述列表` |
| mcp/client.py | 40 | `# TODO[Day8] 调 tools/call，返回结果文本` |
| tools/fs.py | 5 | `# TODO[Day5] 读取文件，超长截断并提示；带行号更利于后续 edit 定位` |
| tools/fs.py | 20 | `# TODO[Day5] 写文件；注意权限层（Day10）后续会拦截工作目录外的写入` |
| tools/more_tools.py | 3 | `每个工具上午讲设计权衡，下午实现。这里只给签名与 TODO，便于你拆到独立文件。` |
| tools/more_tools.py | 12 | `# TODO[Day6] 先实现最稳的 search-replace（old 在文件中唯一时替换为 new）` |
| tools/more_tools.py | 28 | `# TODO[Day6] 调用系统 rg，返回匹配行（带文件名+行号）。与 glob 互补：grep 搜内容，glob 搜路径` |
| tools/more_tools.py | 48 | `# TODO[Day6] 用 pathlib.Path().glob / rglob 找路径` |
| tools/more_tools.py | 59 | `# TODO[Day7] httpx 抓取 -> markdownify 转 markdown -> 截断到预算内` |
| tools/more_tools.py | 65 | `# TODO[Day7] 维护一个结构化待办（add/update/complete），作为模型的 scratchpad` |
| tools/base.py | 61 | `# TODO[Day5] 取消注释并实现：` |
| tools/base.py | 67 | `# TODO[Day6] 再加入完整工具集（→ v1 里程碑）：` |
| tools/base.py | 72 | `# TODO[Day7] 再加入：` |
| tools/shell.py | 7 | `# TODO[Day5] subprocess 执行，捕获 stdout/stderr/returncode，超时保护` |
| tools/shell.py | 8 | `# TODO[Day10] 接入权限层 + 沙箱（bwrap/firejail/docker），危险命令需确认` |
| prompt/render.py | 18 | `# TODO[Day3] 校对你所用模型的真实特殊标记！拼错一个 token，模型行为就会跑偏。` |
| prompt/render.py | 29 | `# TODO[Day3] 设计一个清晰的工具说明格式，并约定模型用` |
| prompt/render.py | 46 | `# TODO[Day3] 把 tools 说明并入 system 段` |
| prompt/render.py | 47 | `# TODO[Day3] 逐条 message 用 ROLE_TOKENS 包裹拼接` |
| prompt/render.py | 48 | `# TODO[Day3] 末尾以 assistant 起始标记结尾，提示模型开始生成` |
| prompt/render.py | 54 | `# TODO[Day3] 用正则/状态机提取所有 <tool_call>...</tool_call>，json.loads 出 name/arguments` |
| skills/loader.py | 32 | `# TODO[Day9] 解析 YAML frontmatter（name/description）+ 正文 body` |
| skills/loader.py | 46 | `# TODO[Day9] 渲染成一段文本，放进系统提示词` |

---

共 **35 处** TODO 注释，分布在 **12 个文件**中。按 Day 分布：

| Day | 数量 |
|-----|------|
| Day3 | 7 |
| Day5 | 6 |
| Day6 | 4 |
| Day7 | 8 |
| Day8 | 4 |
| Day9 | 2 |
| Day10 | 1 |
| 无标记 | 3 |
