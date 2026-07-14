"""完整工具集：edit / grep / glob（Day6，→ v1）+ web_fetch / task_list（Day7）。

每个工具上午讲设计权衡，下午实现。这里只给签名与 TODO，便于你拆到独立文件。
建议最终拆成 edit.py / search.py / web.py / todo.py，再在 base.build_default_registry 注册。

安全加固（Day10+）：
  - edit: 路径遍历防护 + 敏感文件拦截
  - grep: -- 分隔符防止参数注入
  - glob: 路径规范化校验
  - web_fetch: SSRF 防护（内网 IP / 非标准端口拦截）
"""
from __future__ import annotations
from .base import Tool
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse
import ipaddress


# --- edit：三种策略权衡（整文件重写 / unified diff / search-replace）---
def _edit(path: str, old: str = "", new: str = "") -> str:
    # 安全：路径遍历防护 + 敏感文件拦截
    from tools.fs import _resolve_safe, _is_sensitive
    try:
        safe_path = _resolve_safe(path, for_write=True)
    except PermissionError as e:
        return f"[安全拦截] {e}"
    if _is_sensitive(safe_path):
        return f"[安全拦截] 禁止编辑敏感文件：{os.path.basename(path)}"

    with open(safe_path, "r", encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count == 0:
        return f"[失败] 未找到待替换文本，请照抄文件原文（含缩进）。path={path}"
    if count > 1:
        return f"[失败] old 在文件中出现 {count} 次，不唯一；请扩大 old 片段使其唯一。"
    with open(safe_path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return f"已在 {path} 完成 1 处替换。"


# --- grep：基于 ripgrep ---
def _grep(pattern: str, path: str = ".", max_lines: int = 100) -> str:
    try:
        # 使用 -- 分隔符防止 path 被当作 rg 参数
        p = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--", pattern, path],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return "[失败] 未找到 rg，请先安装 ripgrep。"
    if p.returncode not in (0, 1):  # 1 = 无匹配，属正常
        return f"[grep 出错] {p.stderr.strip()}"
    lines = p.stdout.splitlines()
    if not lines:
        return f"[无匹配] pattern={pattern}"
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... [共 {len(lines)} 行，已截断前 {max_lines} 行]"
    return "\n".join(lines)


# --- glob：按文件名模式找文件 ---
def _glob(pattern: str, max_items: int = 100) -> str:
    from tools.fs import _resolve_safe
    # 默认搜索当前目录，确保所有返回路径在工作目录内
    paths = []
    for p in Path(".").rglob(pattern):
        if not p.is_file():
            continue
        abs_p = os.path.realpath(str(p))
        # 校验在工作目录内
        try:
            _resolve_safe(abs_p)
        except PermissionError:
            continue  # 跳过工作目录外的匹配
        paths.append(str(p))
    if not paths:
        return f"[无匹配] pattern={pattern}"
    if len(paths) > max_items:
        return "\n".join(paths[:max_items]) + f"\n... [共 {len(paths)} 个，已截断前 {max_items} 个]"
    return "\n".join(paths)


# ── 出站白名单（注入防护：阻断敏感数据外传）─────────────────────
#
# web_fetch 只允许访问下列域名。空集合 = 允许所有域名（仅由 SSRF 防护兜底）。
# 为获得完整注入防护，请把项目所需的域名加入此集合。
_ALLOWED_OUTBOUND_HOSTS: set[str] = set()
# 示例配置：
# _ALLOWED_OUTBOUND_HOSTS = {"api.deepseek.com", "example.com"}


# ── SSRF 防护：URL 安全校验 ──────────────────────────────────────

# 禁止访问的 IP 范围
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918 私有 A 类
    ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918 私有 B 类
    ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918 私有 C 类
    ipaddress.ip_network("127.0.0.0/8"),       # 回环
    ipaddress.ip_network("169.254.0.0/16"),    # 链路本地（含云元数据）
    ipaddress.ip_network("0.0.0.0/8"),         # 当前网络
    ipaddress.ip_network("224.0.0.0/4"),       # 组播
    ipaddress.ip_network("240.0.0.0/4"),       # 保留
    ipaddress.ip_network("::1/128"),            # IPv6 回环
    ipaddress.ip_network("fe80::/10"),          # IPv6 链路本地
    ipaddress.ip_network("fc00::/7"),           # IPv6 唯一本地
]

# 允许的 URL scheme
_ALLOWED_SCHEMES = {"http", "https"}

# 允许的端口
_ALLOWED_PORTS = {80, 443}


def _validate_url(url: str) -> str | None:
    """校验 URL 安全性。返回 None 表示通过，否则返回拒绝原因。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return f"无法解析 URL：{url}"

    # 1. scheme 校验
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"不允许的协议：{parsed.scheme}（仅允许 http/https）"

    # 2. hostname 校验
    hostname = parsed.hostname
    if not hostname:
        return f"URL 缺少主机名：{url}"

    # 3. 检查 localhost 常见写法
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        return "禁止访问 localhost"

    # 4. 解析 IP 并检查是否为内网地址
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # 不是 IP 是域名——仍需 DNS 解析检查（防 DNS rebinding）
        # 简单策略：拒绝纯数字 IP 的替代写法（如 0x7f000001）
        pass
    else:
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                return f"禁止访问内网/保留 IP：{hostname}"

    # 4. 端口校验
    port = parsed.port
    if port is not None and port not in _ALLOWED_PORTS:
        return f"不允许的端口：{port}（仅允许 80/443）"

    return None


# --- web_fetch：URL -> markdown，控 token 预算 ---
def _web_fetch(url: str, max_tokens: int = 2000) -> str:
    import httpx
    from markdownify import markdownify as md
    from agent.context import truncate_observation
    from tools.fs import wrap_external

    # ── SSRF 安全校验 ──
    rejection = _validate_url(url)
    if rejection is not None:
        return f"[安全拦截] {rejection}"

    # ── 出站白名单校验（注入防护：防数据外传） ──
    if _ALLOWED_OUTBOUND_HOSTS:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname and hostname not in _ALLOWED_OUTBOUND_HOSTS:
            return (
                f"[安全拦截] 出站域名 '{hostname}' 不在白名单中。"
                f"仅允许：{', '.join(sorted(_ALLOWED_OUTBOUND_HOSTS))}"
            )

    resp = httpx.get(url, timeout=20, follow_redirects=False)
    # 处理手动重定向——每步都重新校验目标 URL
    while resp.is_redirect:
        next_url = resp.headers.get("location", "")
        if not next_url:
            break
        # 处理相对路径重定向
        from urllib.parse import urljoin
        next_url = urljoin(url, next_url)
        rejection = _validate_url(next_url)
        if rejection is not None:
            return f"[安全拦截] 重定向目标 {rejection}"
        # 重定向目标也受出站白名单约束
        if _ALLOWED_OUTBOUND_HOSTS:
            parsed = urlparse(next_url)
            hostname = parsed.hostname
            if hostname and hostname not in _ALLOWED_OUTBOUND_HOSTS:
                return (
                    f"[安全拦截] 重定向目标 '{hostname}' 不在白名单中。"
                    f"仅允许：{', '.join(sorted(_ALLOWED_OUTBOUND_HOSTS))}"
                )
        resp = httpx.get(next_url, timeout=20)
    resp.raise_for_status()
    text = md(resp.text)                     # HTML -> markdown
    text = truncate_observation(text, max_chars=max_tokens * 4)
    # 注入隔离：外部内容用 <external> 标签包裹
    return wrap_external(text, url)


# --- task_list（TodoWrite）：自维护待办，提升长任务成功率 ---
#
# 已集成 PlanningLayer（agent/planning.py），内部委托给 PlanningManager
# 保持外部接口不变，同时与 todo_write / todo_update 共享同一状态。

from agent.planning import get_planner, TodoStatus


def _task_list(action: str = "list", items: list | None = None) -> str:
    """维护结构化待办清单（add/update/complete/list）。

    长任务时模型可用此工具分解步骤、逐条推进，避免遗漏或重复。

    action:
      - "add": 添加新待办项。items 为 [{"title": str, "note": str}, ...]
      - "update": 更新待办状态。items 为 [{"id": int, "status": "pending"|"in_progress"|"completed"|"cancelled"}, ...]
      - "list": 列出所有待办（按状态分组）
      - "clear": 清空所有已完成的待办
    """
    planner = get_planner()

    if items is None:
        items = []

    valid_actions = {"add", "update", "list", "clear"}
    if action not in valid_actions:
        return (
            f"[task_list] 无效的 action='{action}'。action 必须是以下之一：add / update / list / clear。\n"
            f"示例：task_list(action='add', items=[{{'title': '第一步', 'note': '说明'}}])\n"
            f"       task_list(action='list')\n"
            f"       task_list(action='update', items=[{{'id': 1, 'status': 'completed'}}])"
        )

    if action == "add":
        added = []
        for item in items:
            todo = planner.add(
                str(item.get("title", "")),
                str(item.get("note", "")),
            )
            added.append(f"  [#{todo.id}] {todo.title}")
        return f"[task_list] 已添加 {len(added)} 项待办：\n" + "\n".join(added)

    elif action == "update":
        updated = 0
        for item in items:
            tid = item.get("id")
            new_status = item.get("status", "pending")
            if planner.update(tid, new_status):
                updated += 1
        return f"[task_list] 已更新 {updated} 项待办状态。"

    elif action == "list":
        return planner.format_for_prompt() or "[task_list] 当前没有待办项。"

    elif action == "clear":
        removed = planner.clear()
        return f"[task_list] 已清理 {removed} 条已完成/已取消的待办。"

    else:
        return f"[task_list] 未知 action：{action}。支持：add / update / list / clear"

# --- 图像识别模块 ---
def image_block(path, media_type="image/png"):
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    # Anthropic 风格内容块（本课端点）
    return {"type": "image", "source":
            {"type": "base64", "media_type": media_type, "data": b64}}

edit_tool = Tool("edit", "编辑文件：把 old 文本替换为 new。",
                 {"type": "object", "properties": {"path": {"type": "string"},
                  "old": {"type": "string"}, "new": {"type": "string"}},
                  "required": ["path", "old", "new"]}, _edit)
grep_tool = Tool("grep", "在文件中搜索匹配 pattern 的行（基于 ripgrep）。",
                 {"type": "object", "properties": {"pattern": {"type": "string"},
                  "path": {"type": "string"}}, "required": ["pattern"]}, _grep)
glob_tool = Tool("glob", "按通配模式查找文件路径。",
                 {"type": "object", "properties": {"pattern": {"type": "string"}},
                  "required": ["pattern"]}, _glob)
web_fetch_tool = Tool("web_fetch", "抓取 URL 并转为 markdown（受 token 预算限制）。",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}, _web_fetch)
task_list_tool = Tool(
    "task_list",
    "维护结构化待办清单，用于分解和跟踪多步骤任务。\n"
    "支持四种操作：\n"
    "  - action='add': 添加新待办项，需传 items=[{title, note}, ...]\n"
    "  - action='update': 更新待办状态，需传 items=[{id, status}, ...]，status 可选 pending/in_progress/completed/cancelled\n"
    "  - action='list': 列出所有待办（按状态分组），无需传 items\n"
    "  - action='clear': 清空已完成/已取消的待办，无需传 items\n"
    "典型流程：先用 action='add' 列出所有步骤，逐条推进时用 action='update' 标记状态，完成后用 action='list' 展示结果。",
    {"type": "object", "properties": {"action": {"type": "string", "description": "操作类型：add / update / list / clear"},
     "items": {"type": "array", "description": "待办项列表（add/update 时必传）"}}, "required": []},
    _task_list,
)
