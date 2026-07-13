# tools/ — 工具层

## 设计决策

工具层是 Agent 与外部世界交互的接口。每个工具封装为一个 `Tool` 数据类，包含 name、description、JSON Schema 参数定义和 `run()` 函数。

### 核心设计：Tool 抽象

```python
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema (OpenAI tools 格式)
    run: Callable     # run(**arguments) -> str
```

**为什么用 dataclass 而非 class 继承**：工具本质上是一组命名的可调用对象，不需要复杂的状态管理。dataclass 足够简洁，且方便序列化为 OpenAI/DeepSeek API 的 tools 参数。

### 工具注册表（ToolRegistry）

简单的 `dict[str, Tool]` 包装，提供：
- `register(tool)`: 注册工具（重名抛异常）
- `get(name)`: 按名称查找
- `schemas()`: 生成 OpenAI tools 格式列表

**为什么不用插件系统**：对 10 天的课程项目，dict 足够。MCP 工具后续通过 `mcp__` 前缀合并进入同一注册表，对主循环透明。

## 工具清单

| 工具 | 文件 | 风险等级 | 安全措施 |
|------|------|---------|---------|
| read | fs.py | 低 | 路径遍历防护 + 敏感文件拦截 |
| write | fs.py | **高** | 路径遍历防护 + 敏感文件拦截 |
| bash | shell.py | **高** | 13 项危险命令拦截 |
| edit | more_tools.py | **高** | 路径遍历防护 + 敏感文件拦截 |
| grep | more_tools.py | 低 | `--` 分隔符防参数注入 |
| glob | more_tools.py | 低 | 工作目录边界过滤 |
| web_fetch | more_tools.py | **高** | SSRF 防护（内网 IP/端口拦截） |
| task_list | more_tools.py | 低 | 会话内内存存储 |
| teacher_search | teacher_search.py | 低 | 只读，CSV 数据源 |
| course_search | course_search.py | 低 | 只读，gpa.json 数据源 |
| save_memory | memory_tools.py | 低 | 文件持久化 + TTL |
| recall_memory | memory_tools.py | 低 | 子串匹配召回 |
| list_memories | memory_tools.py | 低 | 过期自动清理 |
| forget_memory | memory_tools.py | 低 | 安全删除 |

## 安全设计

详见 Day10 安全加固：
- **路径沙箱**：`_resolve_safe()` 确保所有文件访问在工作目录内
- **命令拦截**：`_validate_command()` 阻止 rm -rf、sudo、curl-to-bash 等
- **SSRF 防护**：`_validate_url()` 拦截内网 IP + localhost + 非标准端口
- **参数注入防护**：grep 使用 `--` 分隔符
- **工具配额**：bash/write/edit 限制 30 次/任务

## 扩展指南

添加新工具的步骤：
1. 创建 `tools/your_tool.py`
2. 实现 `_your_tool(**args) -> str`
3. 定义 `your_tool = Tool(name, desc, params, run)`
4. 在 `tools/base.py` 的 `build_default_registry()` 中注册
