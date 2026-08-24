"""FastAPI 基础设施:app factory / middleware / DI / 异常映射。

Phase 1 末尾启用 create_app();具体 router 在 Phase 5 从 ``agentboard.api`` 迁过来。
"""
from __future__ import annotations

from .app import create_app  # noqa: F401
from .middleware import RequestContextMiddleware, RequestLoggingMiddleware  # noqa: F401
from .deps import get_db_session, get_current_user_optional  # noqa: F401
from .errors import register_exception_handlers  # noqa: F401
