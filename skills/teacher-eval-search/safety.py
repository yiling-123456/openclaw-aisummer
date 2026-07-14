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

# 匹配 @[#数字] 格式（markdown 脚注风格，模型可能不遵循 SKILL.md 格式时的兜底）
_BRACKET_CITATION_RE = re.compile(r"@\[#(\d+)\]")


def _normalize_for_match(text: str) -> str:
    """去掉空格和常见中文标点，用于宽松的关键词匹配。"""
    return re.sub(r'[\s,，。！？、；：""''【】《》（）!?.　-]', '', text)


def _keyword_matches(keyword: str, content: str) -> bool:
    """检查关键词是否（近似）出现在原文内容中。

    策略：
    1. 精确子串匹配（最快路径，最严格）
    2. 去空格+标点后的子串匹配（处理标点/空格差异）
    """
    # 1. 精确匹配
    if keyword in content:
        return True
    # 2. 去标点空格后匹配
    nk = _normalize_for_match(keyword)
    nc = _normalize_for_match(content)
    if len(nk) >= 2 and nk in nc:
        return True
    return False


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

        if not _keyword_matches(keyword, review["content"]):
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
                output_parts.append("⚠️")
            elif not _keyword_matches(cit["keyword"], review["content"]):
                output_parts.append("⚠️")
            else:
                verified += 1
                # 不输出 @引用@ 标签，静默通过

    failed = total - verified
    output_parts.append(
        f"\n\n--- 引用验证报告：共 {total} 条，通过 {verified} 条"
        + (f"，失败 {failed} 条" if failed else "，全部通过 ✅")
        + " ---"
    )

    return "".join(output_parts)


def _format_show_all(segments: list[dict[str, Any]], engine: Any) -> str:
    """``-a`` 模式：去除所有 @引用@ 标签，对失败引用原地标注详细信息。"""
    output_parts: list[str] = []
    total = 0
    verified = 0

    for seg in segments:
        output_parts.append(seg["text"])
        for cit in seg["citations"]:
            total += 1
            review = engine.get_review_by_id(cit["id"])
            if review is None:
                output_parts.append(f"⚠️ [序号{cit['id']}不存在]")
            elif not _keyword_matches(cit["keyword"], review["content"]):
                output_parts.append(f"⚠️ [序号{cit['id']}中未找到「{cit['keyword']}」]")
            else:
                verified += 1
                # 不输出 @引用@ 标签，静默通过

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
    4. 所有模式下均去除 ``@引用@`` 标签，仅对校验失败的引用保留 ``⚠️`` 警告。
    5. ``-a`` 模式（``show_all=True``）在校验失败时提供更详细的信息
       （序号不存在 / 关键词未找到），末尾附加引用验证报告。

    Parameters
    ----------
    text:
        大模型输出的原始文本。
    engine:
        TeacherSearchEngine 实例（单例）。
    show_all:
        是否以 ``-a`` 模式展示（失败引用提供更详细的诊断信息）。

    Returns
    -------
    处理后的展示文本（不含 ``@引用@`` 标签）。
    """
    if not text:
        return text

    # 预清理：去除 @[#数字] 格式的标记（markdown 脚注风格，无关键词无法校验，直接去除）
    text = _BRACKET_CITATION_RE.sub('', text)

    if not _CITATION_RE.search(text):
        return text

    # 没有已索引的评论数据 → 无法校验引用真实性。
    # 默认模式下直接去掉 @引用@ 标签（保留干净文本）；
    # -a 模式下保留原始标签供调试。
    # 两种模式下都追加引用总数说明。
    if not engine or not engine.reviews:
        citations = _CITATION_RE.findall(text)
        total = len(citations)
        stripped = text if show_all else _CITATION_RE.sub('', text)
        return stripped + f"\n\n--- 引用验证报告：共 {total} 条（引擎未加载，无法校验真实性） ---"

    segments = _parse_segments(text)

    # ── 在原始文本上检测缺少引用的评价性语句（而非在格式化之后）──
    suspicious = check_uncited_claims(text, engine)

    if show_all:
        result = _format_show_all(segments, engine)
    else:
        result = _format_clean(segments, engine)

    if suspicious:
        result += "\n\n**⚠️ 以下语句可能缺少引用标注：**\n" + "\n".join(
            f"> {s}" for s in suspicious[:5]
        )

    return result


def check_uncited_claims(
    text: str,
    engine: Any,
) -> list[str]:
    """检测评论性语句后是否缺少引用标签（启发式）。

    对包含「老师」「课程」「给分」「作业」「考试」「上课」「讲课」「教学」
    等关键词的句子，检查其后是否紧跟 @数字+关键词@ 引用。

    这只是一个辅助检查——最终的安全判定应结合人工审查。
    """
    eval_keywords = ["老师", "课程", "给分", "作业", "考试", "上课", "讲课", "教学",
                     "课堂", "点名", "给分", "水课", "难度", "平时", "期末"]

    suspicious: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) < 10:
            continue
        # 跳过分隔线和标题
        if line.startswith("#") or line.startswith("==") or line.startswith("--"):
            continue
        # 如果行中已经含有 @N+keyword@ 引用标签，说明有引用，跳过
        if _CITATION_RE.search(line):
            continue
        # 去掉残留的引用标签后，检查是否含评价性关键词
        clean = _CITATION_RE.sub("", line)
        if any(kw in clean for kw in eval_keywords):
            suspicious.append(clean.strip())

    return suspicious
