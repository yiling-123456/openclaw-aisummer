"""Skills 加载器（Day9）。

Skill 与 Tool 的区别：
  - Tool 是一次函数调用（read 一个文件）。
  - Skill 是一包"领域知识 + 操作流程 + 可选脚本/资源"，用一个 SKILL.md 描述，
    在合适的时候被加载进上下文，告诉模型"面对这类任务该怎么一步步做"。

SKILL.md 结构（约定）：
  ---
  name: pdf-report
  description: 一句话说明何时该用这个 skill（用于召回判断）
  ---
  正文：步骤、注意事项、可调用的脚本路径、示例。

加载器要做：扫描 skills/ 下每个含 SKILL.md 的目录，解析 frontmatter，
按需把正文注入系统提示词 / 作为可发现的能力清单。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def parse_skill_md(text: str, path: Path) -> Skill:

    import yaml
    name = description = ""
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)   # 头尾两个 --- 之间是 frontmatter
        meta = yaml.safe_load(fm) or {}
        name = meta.get("name", "")
        description = meta.get("description", "")
    return Skill(name=name, description=description, body=body.strip(), path=path)


def load_skills(root: str = "skills") -> list[Skill]:
    """扫描 root 下所有 SKILL.md。"""
    skills: list[Skill] = []
    for md in Path(root).glob("*/SKILL.md"):
        skills.append(parse_skill_md(md.read_text(encoding="utf-8"), md))
    return skills


def skills_catalog(skills: list[Skill]) -> str:
    """生成给模型看的可用 skill 清单（name + description + 首行摘要），用于按需召回。"""
    if not skills:
        return ""
    lines = ["\n## 可用 Skills"]
    lines.append("以下 Skill 包含领域特定的操作流程。当任务与 Skill 描述相关时，请按照对应 SKILL.md 中的步骤执行：\n")
    for s in skills:
        lines.append(f"- **{s.name}**: {s.description}")
        if s.body:
            # 提取正文首行作为摘要
            first_line = s.body.strip().split("\n")[0].strip()
            if first_line and not first_line.startswith("#"):
                preview = first_line[:120]
                lines.append(f"  > {preview}")
    return "\n".join(lines)
