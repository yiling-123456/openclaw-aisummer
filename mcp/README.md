# mcp/ — MCP 集成层（Day8）

## MCP 协议支持

Model Context Protocol（MCP）让工具集从"写死在代码里"变成"可插拔的外部 server"。

### 实现：stdio + JSON-RPC

```
AgentLoop ──► MCPClient ──► stdio ──► MCP Server (子进程)
                  ▲              ▲
                  │    JSON-RPC  │
                  └──────────────┘
```

**协议细节**：
- `initialize` 握手（2024-11-05 协议版本）
- `tools/list` 拉取工具
- `tools/call` 转发调用
- 每个 MCP 工具以 `mcp__` 前缀注册到 ToolRegistry

### 已接入的 MCP Server

| Server | 用途 | 连接方式 |
|--------|------|---------|
| echo_server.py | 调试用回显（自带） | `python mcp/echo_server.py` |
| @modelcontextprotocol/server-filesystem | 官方文件系统 | `npx -y @modelcontextprotocol/server-filesystem .` |

### 安全加固（Day10）

- **RPC 超时**：30 秒超时保护，防止 server 无响应挂起
- **响应大小限制**：最大 5MB，防止内存溢出
- **上下文管理器**：支持 `with MCPClient(...)` 自动清理
- **析构保护**：`__del__` 确保子进程被 kill
- **错误隔离**：MCP 工具调用失败不污染主循环

### 设计取舍

**为什么用 stdio 而非 HTTP**：
- 零配置（不需端口管理）
- 进程生命周期与 agent 绑定
- JSON-RPC over stdio 是 MCP 规范的标准 transport

**为什么 npx -y 自动安装**：
- 课程演示便利（Day10 后应由用户显式安装，见 CLI 中的 try/except 兜底）
