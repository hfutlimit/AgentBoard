"""Identity service 单元测试。

Phase 4 第一步:验证 features.identity.service 的用户/密码/API Key 行为。
每个测试用 uuid 后缀避免共享 DB 冲突(以后 Phase 8 用 conftest 改造)。
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_identity_tmp.db"

import uuid
import pytest
from fastapi import HTTPException

from agentboard.api_helpers import resolve_actor_context
from agentboard.core.exceptions import Conflict, InvalidValue
from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.identity.service import (
    authenticate_user, change_user_password, create_api_key,
    get_user, get_user_by_username, has_users, list_api_keys,
    list_users, register_user, revoke_api_key, set_user_admin,
    toggle_api_key, update_api_key, update_user_profile,
)


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    # 清旧 DB(前次 test 残留),保证本次 test session 干净
    db_path = "_test_identity_tmp.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    yield
    engine.dispose()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def uname():
    """每个测试拿唯一 username,避免共享 DB 冲突。"""
    def _make(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"
    return _make


def test_first_user_becomes_admin(session, uname):
    # 注意:_init_db 是 module 级,但如果前次会话跑过,本会话一开始就有用户
    # 所以这里只断言"如果 has_users 为 False 时,新用户是 admin"
    n = uname("alice")
    u = register_user(session, username=n, password="secret123")
    session.refresh(u)
    if not has_users(session):  # 仅当是第一个时
        assert u.is_admin is True


def test_register_duplicate_raises_conflict(session, uname):
    n = uname("bob")
    register_user(session, username=n, password="secret123")
    with pytest.raises(Conflict):
        register_user(session, username=n, password="secret456")


def test_register_short_password_rejected(session, uname):
    with pytest.raises(InvalidValue):
        register_user(session, username=uname("charlie"), password="short")


def test_authenticate_correct_password(session, uname):
    n = uname("dave")
    register_user(session, username=n, password="secret123")
    u = authenticate_user(session, username=n, password="secret123")
    assert u is not None
    assert u.username == n


def test_authenticate_wrong_password(session, uname):
    n = uname("eve")
    register_user(session, username=n, password="secret123")
    u = authenticate_user(session, username=n, password="wrong")
    assert u is None


def test_get_user_by_username(session, uname):
    n = uname("frank")
    register_user(session, username=n, password="secret123")
    u = get_user_by_username(session, n)
    assert u is not None
    assert u.username == n


def test_update_user_profile(session, uname):
    n = uname("grace")
    u = register_user(session, username=n, password="secret123")
    email = f"{n}@x.com"
    update_user_profile(session, u, display_name="Grace", email=email)
    session.refresh(u)
    assert u.display_name == "Grace"
    assert u.email == email


def test_change_user_password(session, uname):
    n = uname("henry")
    u = register_user(session, username=n, password="secret123")
    change_user_password(session, u, current_password="secret123", new_password="newpass1234")
    session.refresh(u)
    assert authenticate_user(session, username=n, password="newpass1234") is not None
    assert authenticate_user(session, username=n, password="secret123") is None


def test_change_password_wrong_current(session, uname):
    n = uname("ivy")
    u = register_user(session, username=n, password="secret123")
    with pytest.raises(InvalidValue):
        change_user_password(session, u, current_password="wrong", new_password="newpass1234")


def test_create_and_list_api_key(session, uname):
    n = uname("jack")
    u = register_user(session, username=n, password="secret123")
    key, plaintext = create_api_key(session, user_id=u.id, name="test-key", permissions=["projects:read"])
    assert key.key_prefix.startswith("abk_")
    assert plaintext.startswith("abk_")
    keys = list_api_keys(session, u.id)
    assert len(keys) == 1
    assert keys[0].name == "test-key"


def test_toggle_api_key(session, uname):
    n = uname("kate")
    u = register_user(session, username=n, password="secret123")
    key, _ = create_api_key(session, user_id=u.id, name="k", permissions=[])
    toggle_api_key(session, key.id, u.id, enabled=False)
    session.refresh(key)
    assert key.enabled is False


def test_revoke_api_key(session, uname):
    n = uname("liam")
    u = register_user(session, username=n, password="secret123")
    key, _ = create_api_key(session, user_id=u.id, name="k", permissions=[])
    assert revoke_api_key(session, key.id, u.id) is True
    keys = list_api_keys(session, u.id)
    assert [k.id for k in keys] == [key.id]
    session.refresh(key)
    assert key.enabled is False
    assert key.revoked_at is not None


def test_revoked_api_key_cannot_be_reenabled(session, uname):
    n = uname("liam-reenable")
    u = register_user(session, username=n, password="secret123")
    key, _ = create_api_key(session, user_id=u.id, name="k", permissions=[])
    assert revoke_api_key(session, key.id, u.id) is True

    with pytest.raises(InvalidValue, match="revoked API key"):
        update_api_key(session, key, enabled=True)

    with pytest.raises(InvalidValue, match="revoked API key"):
        toggle_api_key(session, key.id, u.id, enabled=True)

    session.refresh(key)
    assert key.enabled is False


def test_auth_rejects_key_with_revoked_at_even_if_enabled(session, uname):
    n = uname("liam-auth-defense")
    u = register_user(session, username=n, password="secret123")
    key, plaintext = create_api_key(session, user_id=u.id, name="k", permissions=[])
    assert revoke_api_key(session, key.id, u.id) is True

    # Simulate a stale/corrupt write that flips only the enabled flag.
    key.enabled = True
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        resolve_actor_context(f"Bearer {plaintext}", session)

    assert exc_info.value.status_code == 401
    assert key.revoked_at is not None


def test_revoke_api_key_other_user_returns_false(session, uname):
    n1, n2 = uname("mia"), uname("noah")
    u1 = register_user(session, username=n1, password="secret123")
    u2 = register_user(session, username=n2, password="secret123")
    key, _ = create_api_key(session, user_id=u1.id, name="k", permissions=[])
    assert revoke_api_key(session, key.id, u2.id) is False


def test_list_users(session, uname):
    n1, n2 = uname("olivia"), uname("peter")
    register_user(session, username=n1, password="secret123")
    register_user(session, username=n2, password="secret123")
    _, total = list_users(session, limit=1)
    users, _ = list_users(session, limit=200, offset=max(0, total - 200))
    usernames = {u.username for u in users}
    assert n1 in usernames
    assert n2 in usernames


def test_set_user_admin(session, uname):
    n = uname("quinn")
    u = register_user(session, username=n, password="secret123")
    set_user_admin(session, u.id, True)
    session.refresh(u)
    assert u.is_admin is True
    set_user_admin(session, u.id, False)
    session.refresh(u)
    assert u.is_admin is False
