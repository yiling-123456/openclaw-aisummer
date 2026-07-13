"""命令行入口。

用法：
  python -m agent.cli --selfcheck                                  # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"                         # Day5 起：真正跑任务
  python -m agent.cli "介绍张老师" -a                                # 展示完整模型输出含引用验证标记
"""
from __future__ import annotations
import argparse
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("-a", "--show-all", action="store_true",
                    help="显示完整模型输出（含引用验证错误标记）")
    args = p.parse_args(argv)

    if args.selfcheck or not args.task:
        return selfcheck()

    # 真正跑任务：优先用 DeepSeek API；没配 key 时回退到 FakeBackend（离线打通管道）
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

        agent = AgentLoop(backend, reg, system, tracer=tracer)
        result = agent.run(args.task)
        print(f"\n[可观测] 轨迹已保存至 {_trace_path}（可通过 eval.tracer.replay 回放）")

        # ── 引用安全后处理（仅在教师评价数据存在时生效） ──
        # 将 skills/teacher-eval-search 加入 sys.path（teacher_search.py 做过同样的操作）
        _skill_dir = os.path.join(os.path.dirname(__file__), "..", "skills", "teacher-eval-search")
        _skill_dir = os.path.abspath(_skill_dir)
        if _skill_dir not in sys.path:
            sys.path.insert(0, _skill_dir)

        try:
            from search_engine import get_engine  # noqa: E402
            from safety import postprocess_citations  # noqa: E402
            engine = get_engine()
            result = postprocess_citations(result, engine, show_all=args.show_all)
        except Exception:
            pass  # 无教师数据目录或模块不可用 → 原样输出

        # 使用 rich 在终端中渲染 Markdown，让输出更美观
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
