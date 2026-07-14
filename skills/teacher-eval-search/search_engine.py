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

        评价按日期降序排列，优先返回近期评价。max_reviews 在所有匹配教师间
        公平分配（每位教师保底至少返回 1 条近期评价），不会因为排在前面
        的教师评价过多而忽略后面的教师。

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

        # ── 第一遍：发现所有匹配教师 + 收集全局 ID ──────────────
        teacher_ids_map: dict[str, list[int]] = {}  # name → [global_id, ...]

        for query_name in teachers:
            q = query_name.strip().lower()
            matched_names = [n for n in self.teacher_index if q in n.lower()]
            for name in matched_names:
                gids = self.teacher_index[name]
                if department:
                    gids = [
                        gid for gid in gids
                        if department.lower() in self.reviews[gid]["department"].lower()
                    ]
                if not gids:
                    continue
                if name not in teacher_ids_map:
                    teacher_ids_map[name] = []
                teacher_ids_map[name].extend(gids)

        # ── 初始化结果（先建好所有教师的摘要结构） ──────────────
        results: dict[str, Any] = {"teachers": {}, "total_matches": 0, "truncated": False}

        for name, gids in teacher_ids_map.items():
            results["teachers"][name] = self._teacher_summary(name, gids)

        if not results["teachers"]:
            return results

        # ── 第二遍：公平分配，挑近期评价 ──────────────────────
        teacher_count = len(results["teachers"])
        # 每位教师保底至少 1 条；余下额度按"review_count 占比"加权分配
        floor = min(1, max_reviews // max(teacher_count, 1))
        remaining_budget = max(0, max_reviews - floor * teacher_count)

        # 计算总评价数用于加权
        total_all = sum(
            results["teachers"][n]["review_count"] for n in results["teachers"]
        )

        allocations: dict[str, int] = {}
        for name, entry in results["teachers"].items():
            alloc = floor
            if remaining_budget > 0 and total_all > 0:
                weighted = int(
                    remaining_budget * entry["review_count"] / total_all
                )
                alloc += weighted
            allocations[name] = min(alloc, entry["review_count"])

        # 多退少补：如果还有剩余额度，按顺序补给未满的教师
        leftover = max_reviews - sum(allocations.values())
        if leftover > 0:
            for name in results["teachers"]:
                cap = results["teachers"][name]["review_count"]
                if allocations[name] < cap:
                    give = min(leftover, cap - allocations[name])
                    allocations[name] += give
                    leftover -= give
                    if leftover <= 0:
                        break

        # ── 第三遍：取每位教师最晚（近期）的 N 条评价 ──────────
        total_review_count = 0
        for name, entry in results["teachers"].items():
            limit = allocations.get(name, 0)
            if limit <= 0:
                continue

            # 按日期降序排列，取最近的 limit 条
            sorted_gids = sorted(
                teacher_ids_map[name],
                key=lambda gid: self.reviews[gid]["date"],
                reverse=True,
            )
            for gid in sorted_gids:
                if len(entry["reviews"]) >= limit:
                    break
                entry["reviews"].append(self.reviews[gid])
                total_review_count += 1

        # 清理内部字段
        for entry in results["teachers"].values():
            entry.pop("_seen_ids", None)

        results["total_matches"] = len(results["teachers"])
        results["truncated"] = total_review_count >= max_reviews
        return results

    def get_review_by_id(self, review_id: int) -> dict[str, Any] | None:
        """按全局序号取单条评价（供安全校验用）。"""
        if not self._indexed:
            self._build_index()
        return self.reviews.get(review_id)

    # ------------------------------------------------------------------
    # 模糊匹配（输入名称查无此人时，推荐相近姓名）
    # ------------------------------------------------------------------

    @staticmethod
    def _levenshtein_ratio(s1: str, s2: str) -> float:
        """计算两个字符串的归一化编辑距离相似度 (0~1, 1 表示完全相同)。

        使用标准 Levenshtein 距离，除以较长字符串的长度做归一化。
        """
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        if len(s1) > len(s2):
            s1, s2 = s2, s1
        if len(s2) - len(s1) > len(s1):
            return 0.0

        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                cost = 0 if c1 == c2 else 1
                curr_row.append(min(
                    curr_row[j] + 1,
                    prev_row[j + 1] + 1,
                    prev_row[j] + cost,
                ))
            prev_row = curr_row

        distance = prev_row[-1]
        max_len = max(len(s1), len(s2))
        return 1.0 - distance / max_len

    def find_similar_teachers(
        self,
        query_name: str,
        top_k: int = 5,
        threshold: float = 0.4,
    ) -> list[dict[str, Any]]:
        """查找与查询姓名相近的教师名（编辑距离相似度）。

        当 query_name 在全量索引中无匹配时，遍历所有已知教师姓名，
        计算 Levenshtein 相似度，返回最相近的结果供用户选择。

        Args:
            query_name: 查询的教师姓名。
            top_k: 最多返回多少个相似结果。
            threshold: 相似度阈值 (0~1)，低于此值不返回。

        Returns:
            [{"name": str, "departments": [str], "similarity": float}, ...]
            按相似度降序排列。
        """
        if not self._indexed:
            self._build_index()

        candidates: list[tuple[str, float]] = []
        for teacher_name in self.teacher_index:
            sim = self._levenshtein_ratio(query_name, teacher_name)
            if sim >= threshold:
                candidates.append((teacher_name, sim))

        candidates.sort(key=lambda x: -x[1])
        candidates = candidates[:top_k]

        results: list[dict[str, Any]] = []
        for name, sim in candidates:
            gids = self.teacher_index[name]
            depts: set[str] = set()
            for gid in gids:
                depts.add(self.reviews[gid]["department"])
            results.append({
                "name": name,
                "departments": sorted(depts),
                "similarity": round(sim, 3),
            })

        return results

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
                            "net_likes": (int(row[5]) if row[5].isdigit() else 0)
                                        - (int(row[6]) if row[6].isdigit() else 0),
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
