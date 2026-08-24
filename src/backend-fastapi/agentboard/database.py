"""[FACADE] 旧 import 路径:``from agentboard.database import ...``。

实际实现已迁至 ``agentboard.core.infrastructure.database``,本文件仅为
向后兼容保留的薄壳 re-export。新代码请直接 import core.infrastructure。
"""
from .core.infrastructure.database import (  # noqa: F401
    DEFAULT_URL,
    URL,
    engine,
    SessionLocal,
    session_scope,
    get_session,
    init_db,
    UnitOfWork,
    SqlAlchemyUnitOfWork,
)

__all__ = [
    "DEFAULT_URL",
    "URL",
    "engine",
    "SessionLocal",
    "session_scope",
    "get_session",
    "init_db",
    "UnitOfWork",
    "SqlAlchemyUnitOfWork",
]
