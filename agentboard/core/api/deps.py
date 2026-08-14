"""FastAPI dependencies (DI 容器)。

Phase 1 实现 ``get_db_session`` 和 ``get_current_user_optional``;
Phase 5 router 拆分时,各 feature 的 service 注入器(``get_<Feature>_service``)会放这里。
"""
from __future__ import annotations

from typing import Annotated, Iterator

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from ..infrastructure.auth import parse_token
from ..infrastructure.database import get_session
from ..observability.logging import bind_user_id

__all__ = ["get_db_session", "get_current_user_optional", "CurrentUser"]


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency: 每个请求一个 session。"""
    yield from get_session()


CurrentUser = Annotated[int | None, ...]


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
) -> int | None:
    """解析 Bearer token,得到 user_id;失败/缺失返回 None(旧 MCP/Web 兼容)。"""
    if not authorization:
        return None
    token = authorization.removeprefix("Bearer ").strip() if authorization.lower().startswith("bearer ") else authorization
    uid = parse_token(token)
    if uid is not None:
        bind_user_id(uid)
    return uid
