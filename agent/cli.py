"""命令行入口。

用法：
  python -m agent.cli --selfcheck                                  # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"                         # Day5 起：真正跑任务
  python -m agent.cli "介绍张老师" -a                                # 展示完整模型输出含引用验证标记
  python -m agent.cli                                               # 交互式 REPL 模式（无参数时进入）
"""
from __future__ import annotations
import argparse
import json
import os
import sys

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}（Day5 起会变多）")
    except Exception as e:  # noqa
        print(f"[FAIL] 工具注册表：{e}"); ok = False

    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        print("[ok] FakeBackend 可用（未配 DEEPSEEK_API_KEY 时的离线占位后端）")
    except Exception as e:  # noqa
        print(f"[FAIL] FakeBackend：{e}"); ok = False

    try:
        from agent.loop import AgentLoop  # noqa
        print("[ok] 主循环模块可导入（Day5 实现 run 逻辑）")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "[PASS]" if ok else "[FAIL]", "==")
    print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def _postprocess_result(result: str, show_all: bool = False) -> str:
    """对模型输出进行引用安全后处理（仅在教师评价数据存在时生效）。

    所有模式下均去除 @引用@ 标签，确保用户看到干净的输出：
    - 有搜索引擎数据 → 校验引用真实性，通过则去掉标签，失败则标 ⚠️
    - 无搜索引擎数据 → 直接去掉 @引用@ 标签（无法校验，但保证输出干净）
    - -a 模式下校验失败时提供更详细的诊断信息
    """
    import os as _os, sys as _sys
    _skill_dir = _os.path.join(_os.path.dirname(__file__), "..", "skills", "teacher-eval-search")
    _skill_dir = _os.path.abspath(_skill_dir)
    if _skill_dir not in _sys.path:
        _sys.path.insert(0, _skill_dir)

    try:
        from search_engine import get_engine  # noqa: E402
        from safety import postprocess_citations, _CITATION_RE  # noqa: E402
        engine = get_engine()
        return postprocess_citations(result, engine, show_all=show_all)
    except Exception:
        # 搜索引擎完全不可用时：所有模式均去掉 @引用@ 标签
        import re as _re
        result = _re.sub(r"@(\d+)\+(.+?)@", "", result)
        return result


def interactive(backend, registry, system_prompt, tracer=None, show_all: bool = False) -> int:
    """交互式 REPL 模式——类似 Claude Code 的持续对话体验。"""
    # 管道/重定向场景下不使用 Rich（避免编码问题）
    import sys as _sys
    if not _sys.stdin.isatty():
        return _interactive_plain(backend, registry, system_prompt, tracer, show_all=show_all)

    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel
    except ImportError:
        # Fallback: plain text REPL without Rich formatting
        return _interactive_plain(backend, registry, system_prompt, tracer, show_all=show_all)

    # ── 输入层：优先使用 prompt_toolkit（跨平台终端输入支持好）──
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.styles import Style as PTStyle
        from prompt_toolkit.key_binding import KeyBindings
        _USE_PT = True
    except ImportError:
        _USE_PT = False

    console = Console()
    messages = None  # 首次对话从空白开始

    console.print()
    console.print(Panel.fit(
        "[bold cyan]mini-OpenClaw[/] — 交互模式",
        border_style="cyan",
    ))
    console.print(
        "[dim]输入任务描述开始对话，"
        "[bold]/exit[/] 退出  [bold]/clear[/] 清空  [bold]/help[/] 帮助[/]"
    )
    console.print()

    # ── 配置 prompt_toolkit session ──
    if _USE_PT:
        session = PromptSession(history=InMemoryHistory())
        kb = KeyBindings()

        @kb.add("c-c")
        def _handle_ctrl_c(event):
            """Ctrl+C → 退出交互模式"""
            event.app.exit(exception=KeyboardInterrupt)

        @kb.add("c-d")
        def _handle_ctrl_d(event):
            """Ctrl+D → 退出交互模式（空输入时）"""
            if event.current_buffer.text == "":
                event.app.exit(exception=EOFError)
            else:
                # 非空输入时 Ctrl+D 为删除操作
                event.current_buffer.delete()

        @kb.add("escape", "enter")
        def _handle_alt_enter(event):
            """Alt+Enter → 插入换行（多行输入）"""
            event.current_buffer.insert_text("\n")

        pt_style = PTStyle.from_dict({
            "prompt": "bold cyan",
        })

    # ── 获取输入的辅助函数 ──
    def _read_input(prompt_text, is_continuation=False):
        """跨平台输入获取。"""
        if _USE_PT:
            try:
                if is_continuation:
                    return session.prompt(
                        [("class:prompt", "  ")],
                        style=pt_style,
                        key_bindings=kb,
                        multiline=False,
                    ).strip()
                else:
                    return session.prompt(
                        [("class:prompt", "▸ ")],
                        style=pt_style,
                        key_bindings=kb,
                        multiline=False,
                    ).strip()
            except (EOFError, KeyboardInterrupt):
                raise
        else:
            # Fallback: plain input()
            try:
                return input(prompt_text).strip()
            except (EOFError, KeyboardInterrupt):
                raise

    # ── 主循环 ──
    while True:
        try:
            task = _read_input("▸ ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见！[/]")
            break

        if task in ("/exit", "/quit"):
            break

        if task == "/clear":
            messages = None
            console.print("[dim]✓ 对话已清空[/]")
            console.print()
            continue

        if task == "/help":
            console.print("""
[bold]可用命令：[/]
  [bold]/exit[/]    退出交互模式
  [bold]/clear[/]   清空对话历史，开始新对话
  [bold]/help[/]    显示此帮助信息

[bold]快捷键：[/]
  [bold]Ctrl+C[/]    退出交互模式
  [bold]Alt+Enter[/] 插入换行（多行输入）
  [bold]右键[/]      粘贴剪贴板内容

[bold]使用技巧：[/]
  直接输入自然语言任务，Agent 会自动调用工具完成。
  以 [bold]\\[/] 结尾可续行（多行输入）。
  对话上下文在多次输入间自动保留。
""")
            continue

        if not task:
            continue

        # 多行输入支持：以 \ 结尾则续行
        while task.rstrip().endswith("\\"):
            continuation = _read_input("  ", is_continuation=True)
            task = task.rstrip()[:-1] + "\n" + continuation

        # ── 运行 Agent ──
        from agent.loop import AgentLoop
        from agent.permission import PermissionTier, TIER_LABELS
        # Rich 风格的权限询问回调
        def _rich_permission_cb(tool_name: str, tier: PermissionTier, arguments: dict) -> bool:
            label = TIER_LABELS.get(tier, "未知")
            args_str = json.dumps(arguments, ensure_ascii=False)
            if len(args_str) > 120:
                args_str = args_str[:117] + "..."
            try:
                resp = console.input(
                    f"\n[yellow]⚠️ [{label}][/] Agent 要执行 [bold]{tool_name}[/]({args_str})"
                    f"\n[yellow]是否允许？[y/N] [/]"
                ).strip().lower()
                return resp in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                return False
        agent = AgentLoop(
            backend, registry, system_prompt, tracer=tracer,
            permission_callback=_rich_permission_cb,
        )

        try:
            for event_type, data in agent.run_stream(task, messages):
                if event_type == "thinking":
                    console.print("  [dim]⏳ 思考中...[/]", end="\r")

                elif event_type == "tool_call":
                    name = data["name"]
                    args_str = json.dumps(data.get("arguments", {}), ensure_ascii=False)
                    if len(args_str) > 80:
                        args_str = args_str[:77] + "..."
                    console.print(f"  [dim]🔧 {name}({args_str})[/]", end="")

                elif event_type == "tool_result":
                    success = data.get("success", True)
                    icon = "[green]✓[/]" if success else "[red]✗[/]"
                    first_line = data.get("result", "").split("\n")[0][:100]
                    console.print(f" {icon} [dim]{first_line}[/]")

                elif event_type == "done":
                    content = data.get("content", "")
                    messages = data.get("messages")
                    console.print(" " * 30)  # 清掉 "思考中..." 行
                    if content:
                        content = _postprocess_result(content, show_all=show_all)
                        console.print(Markdown(content))
                    console.print()

                elif event_type == "error":
                    console.print(f"  [red]⚠ {data['message']}[/]")

        except Exception as e:
            console.print(f"  [red]运行错误: {e}[/]")
            console.print()

    return 0


def _interactive_plain(backend, registry, system_prompt, tracer=None, show_all: bool = False) -> int:
    """Rich 不可用时的纯文本回退 REPL。"""
    print("\nmini-OpenClaw 交互模式（纯文本）")
    print("输入任务开始，/exit 退出，/clear 清空，/help 帮助\n")

    messages = None

    while True:
        try:
            task = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if task in ("/exit", "/quit"):
            break
        if task == "/clear":
            messages = None
            print("[已清空]")
            continue
        if task == "/help":
            print("  /exit  退出  /clear  清空  /help  帮助  \\  续行")
            continue
        if not task:
            continue

        while task.rstrip().endswith("\\"):
            task = task.rstrip()[:-1] + "\n" + input("  ").strip()

        from agent.loop import AgentLoop
        agent = AgentLoop(backend, registry, system_prompt, tracer=tracer)

        try:
            for event_type, data in agent.run_stream(task, messages):
                if event_type == "tool_call":
                    name = data["name"]
                    args_str = json.dumps(data.get("arguments", {}), ensure_ascii=False)
                    if len(args_str) > 60:
                        args_str = args_str[:57] + "..."
                    print(f"  [tool] {name}({args_str})", end="", flush=True)
                elif event_type == "tool_result":
                    success = data.get("success", True)
                    icon = "OK" if success else "FAIL"
                    print(f" -> {icon}")
                elif event_type == "done":
                    content = data.get("content", "")
                    # 清理 surrogate 字符，防止 print 崩溃
                    try:
                        content = content.encode('utf-8', errors='replace').decode('utf-8')
                    except Exception:
                        pass
                    messages = data.get("messages")
                    print()
                    if content:
                        content = _postprocess_result(content, show_all=show_all)
                        print(content)
                    # ── 成本显示 ──
                    try:
                        print(tracer.cost_summary())
                    except Exception:
                        pass
                    print()
                elif event_type == "error":
                    print(f"  [ERROR] {data['message']}")
        except Exception as e:
            print(f"  [ERROR] {e}\n")

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("-a", "--show-all", action="store_true",
                    help="显示完整模型输出（含引用验证错误标记）")
    args = p.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    # ── 共享的初始化代码（单次模式和交互模式共用）──
    from agent.loop import AgentLoop
    reg = build_default_registry()
    from mcp.client import MCPClient, register_mcp_tools

    echo_mcp = None
    filesystem_mcp = None

    try:
        # 连接 echo server
        try:
            echo_mcp = MCPClient(["python", "mcp/echo_server.py"])
            echo_mcp.start()
            register_mcp_tools(reg, echo_mcp)
            print("[MCP] echo server 已接入")
        except Exception as e:
            print(f"[提示] echo MCP 未接入：{e}")

        # 连接官方 filesystem server
        try:
            import sys as _sys
            _npx = "npx.cmd" if _sys.platform == "win32" else "npx"
            filesystem_mcp = MCPClient([
                _npx,
                "-y",
                "@modelcontextprotocol/server-filesystem",
                ".",
            ])
            filesystem_mcp.start()
            register_mcp_tools(reg, filesystem_mcp)
            print("[MCP] filesystem server 已接入")
        except Exception as e:
            print(f"[提示] filesystem MCP 未接入：{e}")
        try:
            from backend.client import DeepSeekBackend
            backend = DeepSeekBackend()                       # 需要 DEEPSEEK_API_KEY
        except Exception as e:  # noqa
            from backend.fake_backend import FakeBackend
            print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
            backend = FakeBackend()
        from skills.loader import load_skills, skills_catalog
        skills = load_skills()
        system = SYSTEM_PROMPT + "\n\n# 可用 Skills（相关时按其流程执行）\n" + skills_catalog(skills)

        # ── 跨会话记忆注入 ──
        from agent.memory import AgentMemory
        memory = AgentMemory()
        memory_summary = memory.summarize_for_prompt()
        if memory_summary:
            system += memory_summary
            system += "\n（以上记忆来自之前的会话。如果与当前任务相关，请遵循其中的约定和偏好。如果不再适用，可以忽略或使用 forget_memory 工具删除。）\n"

        # ── 轨迹记录（可观测性 D9） ──
        from eval.tracer import Tracer
        import time as _time
        _trace_path = os.path.join(".agent_traces", f"trace_{_time.strftime('%Y%m%d_%H%M%S')}.jsonl")
        os.makedirs(".agent_traces", exist_ok=True)
        tracer = Tracer(_trace_path)

        # ── 交互模式（无任务参数时进入 REPL）──
        if not args.task:
            try:
                return interactive(backend, reg, system, tracer, show_all=args.show_all)
            finally:
                # 安全：确保 MCP 子进程被清理
                if echo_mcp is not None:
                    try:
                        echo_mcp.close()
                    except Exception:
                        pass
                if filesystem_mcp is not None:
                    try:
                        filesystem_mcp.close()
                    except Exception:
                        pass

        # ── 单次执行模式 ──
        agent = AgentLoop(backend, reg, system, tracer=tracer)
        result = agent.run(args.task)
        print(f"\n[可观测] 轨迹已保存至 {_trace_path}（可通过 eval.tracer.replay 回放）")
        # ── 成本显示 ──
        print(tracer.cost_summary())

        # ── 引用安全后处理（仅在教师评价数据存在时生效） ──
        result = _postprocess_result(result, show_all=args.show_all)

        # 使用 rich 在终端中渲染 Markdown，让输出更美观
        # 先清理可能存在的 surrogate 字符（来自原始数据），防止编码崩溃
        try:
            result = result.encode('utf-8', errors='replace').decode('utf-8')
        except Exception:
            pass

        try:
            from rich.console import Console
            from rich.markdown import Markdown
            console = Console()
            console.print(Markdown(result))
        except Exception:
            # Rich 不可用或渲染失败时（Windows GBK 编码问题等），回退到纯文本
            print(result)
    finally:
        # 安全：确保 MCP 子进程被清理
        if echo_mcp is not None:
            try:
                echo_mcp.close()
            except Exception:
                pass
        if filesystem_mcp is not None:
            try:
                filesystem_mcp.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
