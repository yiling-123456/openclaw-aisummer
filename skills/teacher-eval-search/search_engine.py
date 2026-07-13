"""教师评价搜索引擎。

扫描本地 CSV 数据，构建教师名 → 评价列表的内存索引，为 teacher_search 工具
和安全引用校验提供数据支撑。

设计要点：
  - 每所评论分配全局唯一序号（1..N），供大模型在输出中引用（@序号+关键词@）
  - 同名教师可能分属不同院系/拥有不同教师ID，搜索时返回所有匹配并用院系区分
  - 单例模式：230k+ 条评论只索引一次，后续调用复用缓存
"""
from __future__ import annotations
import csv
import os
from pathlib import Path
from typing import Any

# 数据目录名以 chalaoshi_csv 开头
_DATA_DIR_PREFIX = "chalaoshi_csv"


class TeacherSearchEngine:
    """内存索引：扫描所有 CSV，构建教师 → 评价映射。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.reviews: dict[int, dict[str, Any]] = {}       # global_id → review dict
        self.teacher_index: dict[str, list[int]] = {}       # teacher_name → [global_id, ...]
        self._indexed = False

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def search(
        self,
        teachers: list[str],
        department: str = "",
        max_reviews: int = 100,
    ) -> dict[str, Any]:
        """按教师姓名搜索，返回结构化结果。

        Args:
            teachers: 要查询的教师姓名列表（大小写不敏感、子串匹配）。
            department: 可选，按院系过滤（子串匹配 CSV 文件名）。
            max_reviews: 总共最多返回多少条评价原文（跨所有匹配教师）。

        Returns:
            {"teachers": {name: {"departments": [str], "teacher_ids": [int],
                                  "review_count": int,
                                  "reviews": [{"id": int, "date": str,
                                               "likes": int, "dislikes": int,
                                               "content": str}]}},
             "total_matches": int,
             "truncated": bool}
        """
        if not self._indexed:
            self._build_index()

        results: dict[str, Any] = {"teachers": {}, "total_matches": 0, "truncated": False}
        total_review_count = 0

        for query_name in teachers:
            q = query_name.strip().lower()
            matched_names = [n for n in self.teacher_index if q in n.lower()]
            if not matched_names:
                continue

            for name in matched_names:
                global_ids = self.teacher_index[name]

                # 院系过滤
                if department:
                    global_ids = [
                        gid for gid in global_ids
                        if department.lower() in self.reviews[gid]["department"].lower()
                    ]
                    if not global_ids:
                        continue

                if name not in results["teachers"]:
                    results["teachers"][name] = self._teacher_summary(name, global_ids)

                # 追加评价原文（遵守 max_reviews 上限）
                teacher_entry = results["teachers"][name]
                for gid in global_ids:
                    if total_review_count >= max_reviews:
                        results["truncated"] = True
                        break
                    if gid not in teacher_entry["_seen_ids"]:
                        teacher_entry["_seen_ids"].add(gid)
                        teacher_entry["reviews"].append(self.reviews[gid])
                        total_review_count += 1

                if results["truncated"]:
                    break

            if results["truncated"]:
                break

        # 清理内部字段
        for entry in results["teachers"].values():
            entry.pop("_seen_ids", None)
        results["total_matches"] = len(results["teachers"])
        return results

    def get_review_by_id(self, review_id: int) -> dict[str, Any] | None:
        """按全局序号取单条评价（供安全校验用）。"""
        if not self._indexed:
            self._build_index()
        return self.reviews.get(review_id)

    # ------------------------------------------------------------------
    # 内部：索引构建
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """扫描 data_dir 下所有 CSV，分配全局 ID，填充索引。"""
        csv_files = sorted(
            f for f in os.listdir(self.data_dir)
            if f.endswith(".csv") and not f.startswith(".")
        )
        global_id = 0
        for fname in csv_files:
            dept = fname.replace("comment_", "").replace(".csv", "")
            filepath = os.path.join(self.data_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader)  # noqa: F841 — 跳过表头
                    for row in reader:
                        if len(row) < 8:
                            continue
                        global_id += 1
                        teacher_name = row[2].strip()
                        review = {
                            "id": global_id,
                            "teacher_name": teacher_name,
                            "teacher_id": int(row[1]) if row[1].isdigit() else row[1],
                            "department": dept,
                            "date": row[3].strip(),
                            "likes": int(row[5]) if row[5].isdigit() else 0,
                            "dislikes": int(row[6]) if row[6].isdigit() else 0,
                            "content": row[7].strip(),
                        }
                        self.reviews[global_id] = review
                        self.teacher_index.setdefault(teacher_name, []).append(global_id)
            except (csv.Error, UnicodeDecodeError, OSError) as exc:
                print(f"[teacher_search] 警告：跳过无法读取的文件 {fname}：{exc}")
        self._indexed = True

    def _teacher_summary(self, name: str, global_ids: list[int]) -> dict[str, Any]:
        """生成某教师的摘要信息（不包含评价原文）。"""
        depts: set[str] = set()
        tids: set[int] = set()
        for gid in global_ids:
            r = self.reviews[gid]
            depts.add(r["department"])
            if isinstance(r["teacher_id"], int):
                tids.add(r["teacher_id"])
        return {
            "departments": sorted(depts),
            "teacher_ids": sorted(tids),
            "review_count": len(global_ids),
            "reviews": [],
            "_seen_ids": set(),
        }


# ------------------------------------------------------------------
# 模块级单例
# ------------------------------------------------------------------

_engine: TeacherSearchEngine | None = None


def get_engine(data_dir: str | None = None) -> TeacherSearchEngine:
    """获取（或初始化）搜索引擎单例。

    Args:
        data_dir: CSV 数据目录路径。若为 None，自动在当前工作目录及项目根目录下
                  搜索以 chalaoshi_csv 开头的目录。
    """
    global _engine
    if _engine is not None:
        return _engine
    _engine = TeacherSearchEngine(_resolve_data_dir(data_dir))
    return _engine


def _resolve_data_dir(data_dir: str | None) -> str:
    """自动查找数据目录。"""
    if data_dir and os.path.isdir(data_dir):
        return data_dir

    # 按优先级搜索
    candidates: list[str] = []
    if data_dir:
        candidates.append(data_dir)

    # 当前工作目录
    cwd = os.getcwd()
    candidates.append(cwd)

    # 尝试定位项目根目录（向上查找包含 chalaoshi_csv 的目录）
    p = Path(cwd)
    for _ in range(5):
        candidates.append(str(p))
        p = p.parent

    for base in candidates:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            if entry.startswith(_DATA_DIR_PREFIX) and os.path.isdir(os.path.join(base, entry)):
                return os.path.join(base, entry)

    raise FileNotFoundError(
        f"找不到教师评价数据目录（以 '{_DATA_DIR_PREFIX}' 开头）。"
        f"请将数据目录放在项目根目录下，或手动传入 data_dir 参数。"
    )
