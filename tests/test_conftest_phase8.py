"""Phase 8 conftest self-test.

Validates that the shared fixtures in conftest.py work as documented.
"""
import pytest


def test_uname_returns_callable(uname):
    """uname fixture should be a callable that returns a unique name each call."""
    assert callable(uname)
    a = uname()
    b = uname()
    assert a != b
    assert a.startswith("u-")


def test_make_user_creates_user(db_session_override, make_user):
    """make_user factory returns a User with a unique username.

    Note: the very first user in a fresh DB is automatically promoted to
    admin (security convention: avoid lockout). To test a non-admin
    user, register a second one with ``make_user`` (which auto-generates
    a unique name → no first-user collision).
    """
    from agentboard.features.identity.service import get_user_by_username

    # First user is admin by convention; second user is a normal user.
    make_user()  # first → admin
    u = make_user()  # second → not admin
    assert u.id is not None
    assert u.username.startswith("u-")
    assert u.is_admin is False
    # Same session can read it back
    fetched = get_user_by_username(db_session_override, u.username)
    assert fetched is not None
    assert fetched.id == u.id


def test_make_user_admin(db_session_override, make_user):
    u = make_user(is_admin=True)
    assert u.is_admin is True


def test_auth_headers(db_session_override, auth_headers):
    from agentboard import auth

    headers, user = auth_headers
    # auth_headers registers a non-admin user (the second one in the
    # session — the first is auto-admin). To guarantee non-admin,
    # request a name explicitly starting with "user-".
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    # Token decodes back to the same user
    raw = headers["Authorization"].split(" ", 1)[1]
    assert auth.parse_token(raw) == user.id


def test_admin_headers(db_session_override, admin_headers):
    headers, user = admin_headers
    assert user.is_admin is True


def test_uname_callable_returns_unique(uname):
    """Two calls should produce different values (uuid-based)."""
    a = uname()
    b = uname()
    assert a != b
    assert a.startswith("u-")
    assert b.startswith("u-")
