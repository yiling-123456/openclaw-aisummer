"""教师评价检索 Skill —— 本地数据搜索 + 引用安全校验。"""
from .search_engine import TeacherSearchEngine, get_engine
from .safety import verify_citations, CitationError

__all__ = ["TeacherSearchEngine", "get_engine", "verify_citations", "CitationError"]
