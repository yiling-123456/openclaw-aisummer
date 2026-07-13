"""引用安全校验模块。

对大模型生成的教师评价输出进行后处理（或工具调用后检查）：
  1. 提取所有 @序号+关键词@ 引用标签
  2. 校验序号是否存在于原始数据
  3. 校验关键词是否真正出现在对应评价原文中
  4. 检测缺少引用的评价性语句

这是 SKILL.md 步骤 4（输出前置安全拦截）的代码实现。
"""
from __future__ import annotations
import re
from typing import Any


# 匹配 @数字+任意文本@ （非贪婪，不允许嵌套）
_CITATION_RE = re.compile(r"@(\d+)\+(.+?)@")


class CitationError(Exception):
    """引用校验失败时抛出的异常。"""
    pass


def verify_citations(
    text: str,
    engine: Any,  # TeacherSearchEngine，避免循环导入
) -> dict[str, Any]:
    """校验文本中所有 @序号+关键词@ 引用标签。

    Args:
        text: 大模型生成的输出文本。
        engine: TeacherSearchEngine 实例，用于按 ID 查找原始评价。

    Returns:
        {
            "valid": bool,           # 全部引用是否通过校验
            "total": int,            # 引用总数
            "verified": int,         # 通过数
            "violations": [          # 违规列表
                {
                    "citation": str,     # 原始引用文本
                    "id": int,           # 序号
                    "keyword": str,      # 关键词
                    "reason": str,       # 违规原因
                }
            ],
        }
    """
    citations = _CITATION_RE.findall(text)  # [(id_str, keyword), ...]
    violations: list[dict[str, Any]] = []
    verified_count = 0

    for id_str, keyword in citations:
        review_id = int(id_str)
        citation_text = f"@{review_id}+{keyword}@"
        review = engine.get_review_by_id(review_id)

        if review is None:
            violations.append({
                "citation": citation_text,
                "id": review_id,
                "keyword": keyword,
                "reason": f"序号 {review_id} 不存在于原始数据中",
            })
            continue

        if keyword not in review["content"]:
            violations.append({
                "citation": citation_text,
                "id": review_id,
                "keyword": keyword,
                "reason": f"关键词「{keyword}」未出现在序号 {review_id} 的原始评价中",
            })
            continue

        verified_count += 1

    return {
        "valid": len(violations) == 0,
        "total": len(citations),
        "verified": verified_count,
        "violations": violations,
    }


# =========================================================================
# 输出后处理 —— 引用验证 + 内容过滤
# =========================================================================


def _parse_segments(text: str) -> list[dict[str, Any]]:
    """将带引用的文本按 (声明 → 引用列表) 切分为结构化段落。

    每个段落格式::

        {"text": str, "citations": [{"str": str, "id": int, "keyword": str}, ...]}

    相邻的引用（中间无其它文字）会被视为多源引用，合并到同一段落。
    """
    segments: list[dict[str, Any]] = []
    cursor = 0

    for m in _CITATION_RE.finditer(text):
        preceding = text[cursor:m.start()]
        citation = {
            "str": m.group(0),
            "id": int(m.group(1)),
            "keyword": m.group(2),
        }

        if not preceding and segments:
            # 与前一个引用共享 claim 文本（多源引用 @...@@...@）
            segments[-1]["citations"].append(citation)
        else:
            segments.append({
                "text": preceding,
                "citations": [citation],
            })
        cursor = m.end()

    # 尾部文本（最后一个引用之后无引用的残余文字）
    trailing = text[cursor:]
    if trailing:
        segments.append({
            "text": trailing,
            "citations": [],
        })

    return segments


def _format_clean(segments: list[dict[str, Any]], engine: Any) -> str:
    """默认模式（无 ``-a``）：去除所有 @引用@ 标签，仅对校验失败的引用保留 ⚠️ 警告。"""
    output_parts: list[str] = []
    total = 0
    verified = 0

    for seg in segments:
        output_parts.append(seg["text"])
        for cit in seg["citations"]:
            total += 1
            review = engine.get_review_by_id(cit["id"])
            if review is None:
                output_parts.append(
                    f"⚠️ [引用验证失败：序号{cit['id']}不存在于原始数据中]"
                )
            elif cit["keyword"] not in review["content"]:
                output_parts.append(
                    f"⚠️ [引用验证失败：序号{cit['id']}中未找到关键词「{cit['keyword']}」]"
                )
            else:
                verified += 1
                # 默认模式不输出 @引用@ 标签，静默通过

    if total > 0:
        failed = total - verified
        output_parts.append(
            f"\n\n--- 引用验证报告：共 {total} 条，通过 {verified} 条"
            + (f"，失败 {failed} 条" if failed else "，全部通过 ✅")
            + " ---"
        )

    return "".join(output_parts)


def _format_show_all(segments: list[dict[str, Any]], engine: Any) -> str:
    """``-a`` 模式：保留完整输出，对失败引用原地标注。"""
    output_parts: list[str] = []
    total = 0
    verified = 0

    for seg in segments:
        output_parts.append(seg["text"])
        for cit in seg["citations"]:
            total += 1
            review = engine.get_review_by_id(cit["id"])
            if review is None:
                output_parts.append(
                    f"⚠️ {cit['str']}[引用验证失败：序号{cit['id']}不存在于原始数据中]"
                )
            elif cit["keyword"] not in review["content"]:
                output_parts.append(
                    f"⚠️ {cit['str']}[引用验证失败：序号{cit['id']}中未找到关键词「{cit['keyword']}」]"
                )
            else:
                verified += 1
                output_parts.append(cit["str"])

    if total > 0:
        failed = total - verified
        output_parts.append(
            f"\n\n--- 引用验证报告：共 {total} 条，通过 {verified} 条"
            + (f"，失败 {failed} 条" if failed else "，全部通过 ✅")
            + " ---"
        )

    return "".join(output_parts)


def postprocess_citations(
    text: str,
    engine: Any,
    show_all: bool = False,
) -> str:
    """后处理模型输出：校验引用并生成适合用户阅读的展示文本。

    流程
    ----
    1. 解析所有 ``@N+keyword@`` 引用标签
    2. 按引用将文本切分为 (声明文本 → 引用列表) 段落
    3. 逐条校验：序号存在？关键词在原文中？
    4. **默认模式**（``show_all=False``）：去除所有 ``@引用@`` 标签，
       仅对校验失败的引用保留 ``⚠️`` 警告，末尾附加引用验证报告。
    5. **舒展模式**（``show_all=True``，对应 CLI ``-a`` 参数）：保留完整
       的 ``@引用@`` 标签，对失败引用原地标注 ``⚠️`` 警告。

    Parameters
    ----------
    text:
        大模型输出的原始文本。
    engine:
        TeacherSearchEngine 实例（单例）。
    show_all:
        是否以 ``-a`` 模式完整展示（含 ``@引用@`` 标签）。

    Returns
    -------
    处理后的展示文本。
    """
    if not text:
        return text
    if not _CITATION_RE.search(text):
        return text  # 没有引用标签，无需处理
    # 没有已索引的评论数据 → 无法校验，原样返回（可能是非教师评价任务）
    if not engine or not engine.reviews:
        return text

    segments = _parse_segments(text)

    if show_all:
        return _format_show_all(segments, engine)
    else:
        return _format_clean(segments, engine)


def check_uncited_claims(
    text: str,
    engine: Any,
) -> list[str]:
    """检测评论性语句后是否缺少引用标签（启发式）。

    对包含「老师」「课程」「给分」「作业」「考试」「上课」「讲课」「教学」
    等关键词的句子，检查其后是否紧跟 @数字+关键词@ 引用。

    这只是一个辅助检查——最终的安全判定应结合人工审查。
    """
    # 把已引用部分移除，检查剩余文本
    clean = _CITATION_RE.sub("", text)

    eval_keywords = ["老师", "课程", "给分", "作业", "考试", "上课", "讲课", "教学",
                     "课堂", "点名", "给分", "水课", "难度", "平时", "期末"]

    suspicious: list[str] = []
    for line in clean.splitlines():
        line = line.strip()
        if not line or len(line) < 10:
            continue
        # 跳过分隔线和标题
        if line.startswith("#") or line.startswith("==") or line.startswith("--"):
            continue
        if any(kw in line for kw in eval_keywords):
            suspicious.append(line)

    return suspicious
