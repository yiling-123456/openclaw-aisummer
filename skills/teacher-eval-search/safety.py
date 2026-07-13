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
