"""最小 MCP 客户端（Day8）。

MCP（Model Context Protocol）让工具集从"写死在代码里"变成"可插拔的外部 server"。
本文件实现一个最小客户端：通过 stdio 跟 server 通信，做 JSON-RPC。

要实现的握手与调用：
  1. 启动 server 子进程（stdio transport）
  2. initialize 握手
  3. tools/list  —— 拉取 server 暴露的工具
  4. tools/call  —— 把某次调用转发给 server，拿回结果
然后在 agent/loop 里，把这些 MCP 工具**透明合并**进内置 ToolRegistry。

安全加固（Day10+）：
  - RPC 超时保护（防止 server 无响应导致永久挂起）
  - 上下文管理器 + 析构清理（防止子进程泄漏）
  - 输入大小限制（防止恶意 server 返回超大响应）
"""
from __future__ import annotations
import json
import subprocess
import signal
from typing import Any

from tools.base import Tool, ToolRegistry

# RPC 调用超时（秒）
_RPC_TIMEOUT = 30.0
# 单次 RPC 响应最大字节数
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB


class MCPClient:
    def __init__(self, command: list[str]):
        self.command = command
        self.proc: subprocess.Popen | None = None
        self._id = 0

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,          # 行缓冲，配合一行一条消息
        )
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mini-openclaw", "version": "0.1"},
        })
        self._notify("notifications/initialized")   # 通知，无需等 result

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError(f"MCP server 已退出（退出码: {self.proc.returncode if self.proc else 'N/A'}）")

        self._id += 1
        req = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}},
            ensure_ascii=False,
        )
        try:
            self.proc.stdin.write(req + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"无法写入 MCP server stdin（server 可能已崩溃）：{e}")

        try:
            # 使用 communicate 式的手动超时——Popen.communicate 不适合逐行读取
            import threading
            result: list[str | None] = [None]
            error: list[Exception | None] = [None]

            def _read():
                try:
                    result[0] = self.proc.stdout.readline()
                except Exception as e:
                    error[0] = e

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout=_RPC_TIMEOUT)

            if t.is_alive():
                raise TimeoutError(f"MCP RPC '{method}' 在 {_RPC_TIMEOUT}s 内无响应")

            if error[0]:
                raise error[0]

            line = result[0]
            if not line:
                raise RuntimeError(f"MCP server 在 RPC '{method}' 期间关闭了 stdout")

            # 防止超大响应撑爆内存
            if len(line) > _MAX_RESPONSE_BYTES:
                raise RuntimeError(f"MCP RPC 响应过大（{len(line)} 字节），已拒绝")

            resp = json.loads(line)
        except TimeoutError:
            raise
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MCP RPC 响应 JSON 解析失败：{e}")
        except Exception:
            raise

        if "error" in resp:
            raise RuntimeError(f"MCP RPC 错误（method={method}）：{resp['error']}")
        return resp.get("result")

    def _notify(self, method: str, params: dict | None = None) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return  # 通知类消息，server 已退出则静默跳过
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}  # 无 id
        try:
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # 通知失败不影响主流程

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts)

    def close(self) -> None:
        """安全关闭 MCP 子进程。"""
        if self.proc is None:
            return
        # 先尝试优雅终止，避免 EPIPE 错误
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            pass
        # 如果还活着，强制杀死
        if self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
        # 最后关闭管道
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        """析构时确保子进程被清理。"""
        try:
            self.close()
        except Exception:
            pass



def register_mcp_tools(registry: ToolRegistry, client: MCPClient) -> None:
    """把一个 MCP server 的工具包装成内置 Tool 并注册，实现透明合并。"""
    def _make_runner(tool_name: str):
        """工厂函数：为每个工具创建独立的 runner（避免闭包晚绑定）。"""
        def _run(**kwargs):
            try:
                return client.call_tool(tool_name, kwargs)
            except TimeoutError:
                return f"[MCP 超时] 工具 '{tool_name}' 在 {_RPC_TIMEOUT}s 内未响应"
            except Exception as e:
                return f"[MCP 错误] 工具 '{tool_name}' 执行失败：{e}"
        return _run

    for spec in client.list_tools():
        name = spec["name"]
        registry.register(Tool(
            name=f"mcp__{name}",            # 命名空间避免和内置工具撞名
            description=spec.get("description", ""),
            parameters=spec.get("inputSchema", {"type": "object", "properties": {}}),
            run=_make_runner(name),
        ))
