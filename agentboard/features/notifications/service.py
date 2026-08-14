"""Notifications service。

Phase 4 第五段:从 service.py 拆分。本文件仅作 facade 装载新模块;老 import
路径由 service.py 末尾 ``from .features.X.service import *`` 重绑保持兼容。

本文件不实现业务逻辑,只是把 service.py 里同主题的函数搬家过来 + 加必要的
import,行为完全一致。
"""
from __future__ import annotations

import logging

from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容

log = logging.getLogger("agentboard.features.notifications.service")

from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
    InvalidValue,
    NotFound,
)

from ...core.service_helpers import (
    _commit, _invalidate_project_stats_cache, _paginate, _required,
)


from ..identity.models import Notification, User

def delete_notification(s: Session, notif_id: int, user_id: int) -> bool:
    n = s.get(Notification, notif_id)
    if not n or n.user_id != user_id:
        return False
    s.delete(n); _commit(s); return True


# ---------- Project statistics ----------

def create_notification(
    s: Session, *, user_id: int, notif_type: str, title: str,
    content: str = "", link: str | None = None,
) -> Notification:
    if not s.get(models.User, user_id):
        raise NotFound(f"user {user_id} not found")
    valid_types = {
        "project_invite", "join_request", "task_assigned", "status_changed", "mentioned",
    }
    if notif_type not in valid_types:
        raise InvalidValue(f"notification type must be one of: {valid_types}")
    n = Notification(
        user_id=user_id, type=notif_type, title=title,
        content=content, link=link,
    )
    s.add(n); _commit(s); s.refresh(n); return n


def search_notifications(s: Session, user_id: int, q: str, limit: int = 20):
    """当前用户通知关键词搜索（title/content），供命令面板等场景使用（v6.15）。

    通知属用户隐私数据，必须按 user_id 隔离，仅返回本人通知。
    """
    like = f"%{q}%"
    qry = (
        s.query(Notification)
        .filter(Notification.user_id == user_id,
                or_(Notification.title.ilike(like), Notification.content.ilike(like)))
        .order_by(Notification.created_at.desc())
    )
    return qry.limit(limit).all()


def mark_notification_read(s: Session, notif_id: int, user_id: int) -> Notification | None:
    n = s.get(Notification, notif_id)
    if not n or n.user_id != user_id:
        return None
    n.is_read = True; _commit(s); s.refresh(n); return n


def list_notifications(
    s: Session, user_id: int, limit: int | None = None, offset: int = 0,
    unread_only: bool = False,
) -> tuple[list, int]:
    q = s.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    total = q.count()
    return _paginate(q.order_by(Notification.created_at.desc()), limit, offset).all(), total


def mark_all_notifications_read(s: Session, user_id: int) -> int:
    count = (
        s.query(Notification)
        .filter(Notification.user_id == user_id, Notification.is_read == False)
        .update({"is_read": True})
    )
    _commit(s); return count


