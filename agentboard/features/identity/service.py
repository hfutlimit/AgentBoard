"""Identity/Auth service。

从 service.py 拆出的用户与 API Key 相关函数。Phase 4 第一步:独立、自包含、
不依赖其他 feature,适合做拆分样板。

使用::

    from agentboard.features.identity.service import register_user, get_user
    u = register_user(session, username="alice", password="...")
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ...core import infrastructure
from ...core.exceptions import Conflict, InvalidValue
auth = infrastructure.auth  # 简化使用,等价 from ...core.infrastructure import auth
from ...core.service_helpers import _commit
from ... import models  # 老 facade,仍用
from .models import ApiKey, User

log = logging.getLogger("agentboard.features.identity.service")

from ...core.exceptions import (
    Duplicate,
)


# 旧 service.Duplicate 的别名,新代码用 Conflict(Duplicate 是 Conflict 的 alias)
Duplicate = Conflict


# ---- 内部 helper ---------------------------------------------------------

def has_users(s: Session) -> bool:
    return s.query(User.id).first() is not None


# ---- 用户管理 ------------------------------------------------------------

def register_user(s: Session, *, username: str, password: str) -> User:
    """注册用户。第一个注册的自动成为管理员。"""
    username = username.strip() if username else ""
    if not username:
        raise InvalidValue("username is required")
    if len(username) > 64:
        raise InvalidValue("username must be at most 64 characters")
    if len(password or "") < 8:
        raise InvalidValue("password must be at least 8 characters")
    if s.query(User).filter_by(username=username).first():
        raise Duplicate(f"username '{username}' already exists")
    is_first = not has_users(s)
    u = User(username=username, password_hash=auth.hash_password(password), is_admin=is_first)
    s.add(u)
    _commit(s, duplicate=f"username '{username}' already exists")
    s.refresh(u)
    log.info("user_registered", extra={"user_id": u.id, "is_admin": is_first})
    return u


def authenticate_user(s: Session, *, username: str, password: str) -> User | None:
    """验证用户名+密码,成功返回 User,失败返回 None。"""
    u = s.query(User).filter_by(username=username).first()
    if u and auth.verify_password(password, u.password_hash):
        if auth.password_needs_rehash(u.password_hash):
            u.password_hash = auth.hash_password(password)
            _commit(s)
        return u
    return None


def get_user(s: Session, id: int) -> User | None:
    return s.get(User, id)


def get_user_by_username(s: Session, username: str) -> User | None:
    return s.query(User).filter(User.username == username).first()


def update_user_profile(
    s: Session, user: User, *, display_name: str | None = None,
    email: str | None = None, avatar_url: str | None = None,
) -> User:
    if display_name is not None:
        user.display_name = display_name.strip()[:100]
    if email is not None:
        normalized_email = email.strip().lower() or None
        if normalized_email:
            existing = s.query(User).filter(
                User.email == normalized_email, User.id != user.id,
            ).first()
            if existing:
                raise Duplicate(f"email '{normalized_email}' already exists")
        user.email = normalized_email
    if avatar_url is not None:
        user.avatar_url = avatar_url.strip() or None
    _commit(s, duplicate="email already exists")
    s.refresh(user)
    return user


def change_user_password(
    s: Session, user: User, *, current_password: str, new_password: str,
) -> None:
    if not auth.verify_password(current_password, user.password_hash):
        raise InvalidValue("current password is incorrect")
    if len(new_password or "") < 8:
        raise InvalidValue("new password must be at least 8 characters")
    user.password_hash = auth.hash_password(new_password)
    _commit(s)


# ---- API Key -------------------------------------------------------------

def create_api_key(
    s: Session, *, user_id: int, name: str, permissions: list[str],
) -> tuple[ApiKey, str]:
    """创建 API Key,返回 (ApiKey 实例, 明文 key)。

    明文只返回一次,后续只能用 prefix + digest 查询。
    """
    plaintext, prefix, digest = auth.generate_api_key()
    item = ApiKey(
        user_id=user_id, name=name.strip(), key_prefix=prefix, key_hash=digest,
        permissions=auth.encode_permissions(permissions), enabled=True,
    )
    s.add(item)
    _commit(s)
    s.refresh(item)
    return item, plaintext


def list_api_keys(s: Session, user_id: int) -> list[ApiKey]:
    return s.query(ApiKey).filter(ApiKey.user_id == user_id).order_by(ApiKey.id.desc()).all()


def revoke_api_key(s: Session, key_id: int, user_id: int) -> bool:
    """撤销(删除)API Key,只能删自己的。返回是否成功。"""
    k = s.get(ApiKey, key_id)
    if not k or k.user_id != user_id:
        return False
    s.delete(k)
    _commit(s)
    return True


def toggle_api_key(s: Session, key_id: int, user_id: int, enabled: bool) -> ApiKey | None:
    k = s.get(ApiKey, key_id)
    if not k or k.user_id != user_id:
        return None
    k.enabled = enabled
    _commit(s)
    s.refresh(k)
    return k


# ---- 管理员用户管理 ------------------------------------------------------

def list_users(s: Session, limit: int | None = None, offset: int = 0) -> tuple[list[User], int]:
    from ...core.service_helpers import _paginate
    from sqlalchemy import func
    q = s.query(User).order_by(User.id.asc())
    total = s.query(func.count(User.id)).scalar() or 0
    return _paginate(q, limit, offset).all(), total


def set_user_admin(s: Session, user_id: int, is_admin: bool) -> User | None:
    u = s.get(User, user_id)
    if not u:
        return None
    u.is_admin = is_admin
    _commit(s)
    s.refresh(u)
    return u


def get_user_by_id(s: Session, user_id: int) -> User | None:
    """兼容老 auth.get_user_by_id 的语义。"""
    return s.get(User, user_id)


# ---- 内部小工具 ---------------------------------------------------------

def _count_query(s: Session, model) -> int:
    from sqlalchemy import func
    return s.query(func.count(model.id)).scalar() or 0


# 替换 list_users 里用的 func_count 别名(已废弃,内联)
_ = "deprecated"


# ---- 同步自 service.py ----
def update_api_key(
    s: Session, item: ApiKey, *, name: str | None = None,
    enabled: bool | None = None, permissions: list[str] | None = None,
) -> ApiKey:
    if name is not None:
        item.name = name.strip()
    if enabled is not None:
        item.enabled = enabled
    if permissions is not None:
        item.permissions = auth.encode_permissions(permissions)
    item.updated_at = models._now()
    _commit(s)
    s.refresh(item)
    return item

# ---- 同步自 service.py ----
def get_api_key(s: Session, *, user_id: int, api_key_id: int) -> ApiKey | None:
    return s.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()

# ---- 同步自 service.py ----
def lookup_api_key_by_hash(s: Session, key_hash: str) -> ApiKey | None:
    return s.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()

# ---- 同步自 service.py ----
def touch_api_key(s: Session, item: ApiKey) -> None:
    item.last_used_at = models._now()
    _commit(s)

# ---- 同步自 service.py ----
def paginated_result(items: list, total: int) -> dict:
    return {"items": items, "total": total}