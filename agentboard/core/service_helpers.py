"""Service 层公共 helper(从 service.py 拆出,所有 feature 共享)。

- ``_required`` / ``_paginate`` / ``_commit`` / ``_check_*`` 等纯函数工具
- 不放 SQLAlchemy session 生命周期管理(UoW 在 Phase 4 末启用)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from .common.enums import (
    ALL_PRIORITIES, ALL_STATUSES, ALL_TYPES, Priority, Status,
)
from .exceptions import Conflict, InvalidValue
from .infrastructure.cache import get_cache

log = logging.getLogger("agentboard.core.service_helpers")

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200


# ---- 校验 ----------------------------------------------------------------

def _required(value: str, field: str, max_length: int) -> str:
    """必填字符串字段,strip + 长度校验。"""
    value = (value or "").strip()
    if not value:
        raise InvalidValue(f"{field} is required")
    if len(value) > max_length:
        raise InvalidValue(f"{field} must be at most {max_length} characters")
    return value


def _check_type(value: str) -> None:
    if value not in ALL_TYPES:
        raise InvalidValue(f"invalid type '{value}'")


def _check_status(value: str) -> None:
    if value not in ALL_STATUSES:
        raise InvalidValue(f"invalid status '{value}'")


def _check_priority(priority: str) -> None:
    if priority not in ALL_PRIORITIES:
        raise InvalidValue(f"invalid priority '{priority}'")


# ---- 分页 ----------------------------------------------------------------

def _paginate(q: Query, limit: int | None, offset: int) -> Query:
    """统一的 limit/offset 校验 + 应用。"""
    if offset < 0:
        raise InvalidValue("offset must be non-negative")
    actual_limit = DEFAULT_PAGE_SIZE if limit is None else limit
    if actual_limit < 1 or actual_limit > MAX_PAGE_SIZE:
        raise InvalidValue(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    return q.limit(actual_limit).offset(offset)


# ---- 提交 + 缓存失效 ------------------------------------------------------

def _commit(s: Session, *, duplicate: str | None = None) -> None:
    """统一 commit 入口,处理 IntegrityError → Conflict(Duplicate alias)。"""
    try:
        s.commit()
    except IntegrityError as e:
        s.rollback()
        if duplicate:
            raise Conflict(duplicate) from e
        raise


def _invalidate_project_stats_cache(project_id: int) -> None:
    """项目统计缓存失效(任务/Story 变更后调用)。

    失败也无所谓:缓存项可能不存在。
    """
    get_cache().invalidate_prefix(f"project_stats:{project_id}")


# ---- 日期解析 ------------------------------------------------------------

def _parse_due_date(value: Any) -> date | None:
    """Convert ISO date string (YYYY-MM-DD) to date object; pass through None/date."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise InvalidValue(f"invalid due_date format: {value!r}, expected YYYY-MM-DD")


# ---- 反向兼容别名(老 service.py 用的下划线名字) ------------------------

# 老的 service.py 函数式 import 形式是 ``from . import service; service._commit(s)``。
# 保持同名让 facade/service.py 可以直接 ``from agentboard.core.service_helpers import _commit``。
__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "_required",
    "_check_type",
    "_check_status",
    "_check_priority",
    "_paginate",
    "_commit",
    "_invalidate_project_stats_cache",
    "_parse_due_date",
]
