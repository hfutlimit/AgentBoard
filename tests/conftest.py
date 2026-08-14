"""Shared pytest helpers for the AgentBoard test suite (Phase 8).

Scope: only minimal, low-risk shared utilities. We deliberately do NOT
provide a global ``client`` / ``db_session`` fixture here because:

1. Python 3.14's stdlib ``datetime`` no longer has a top-level ``datetime.now``;
   some code paths under the FastAPI app still rely on the old form, and a
   full app import under a shared in-memory engine triggers that path.
2. The unit tests (tests/unit/) already follow a per-file pattern with
   ``init_db()`` + module-scoped ``AGENTBOARD_DB_URL``; forcing a global
   fixture on them would be churn for no benefit.

What lives here:

- :func:`uname` — generate a unique username (uuid suffix) for test isolation
- :func:`make_user` — factory fixture that creates a user with a unique name
- :func:`auth_headers` / :func:`admin_headers` — bearer-token header factories

These are pure-Python helpers, no DB schema, no app import — safe to use
from any test.

For full REST client tests, see existing patterns in
``tests/test_admin_api_key_scope.py`` (StaticPool + dependency_overrides)
and ``tests/test_smoke.py`` (TestClient in a context manager).
"""
from __future__ import annotations

import uuid
import pytest


@pytest.fixture
def uname():
    """Generate a unique username (uuid suffix) for test isolation.

    Returns a callable that produces a fresh name on each call, so a test
    can register several users without conflict:

        def test_foo(uname):
            u1 = uname()
            u2 = uname()
            assert u1 != u2
    """
    return lambda: f"u-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def make_user(db_session_override):
    """Factory: create a user with a unique username.

    Requires the ``db_session_override`` fixture (a per-test SQLAlchemy
    session). If your test doesn't need a session, use ``uname`` directly.

    Usage:

        def test_foo(make_user, auth_headers):
            u = make_user()
            assert u.username.startswith("u-")
    """
    from agentboard.features.identity.service import register_user

    def _make(username=None, password="test1234", is_admin=False):
        username = username or f"u-{uuid.uuid4().hex[:8]}"
        u = register_user(db_session_override, username=username, password=password)
        if is_admin:
            from agentboard.features.identity.service import set_user_admin
            set_user_admin(db_session_override, u.id, True)
            db_session_override.refresh(u)
        return u
    return _make


@pytest.fixture
def db_session_override():
    """Per-test SQLAlchemy session.

    Each invocation creates a FRESH in-memory SQLite engine (StaticPool) and
    builds the schema from scratch. This is the only way to get real test
    isolation with the shared in-memory engine pattern; sharing an engine
    across tests contaminates state.

    Tests that opt in to ``make_user`` / ``auth_headers`` get a session bound
    to this fresh engine. Engine is disposed at teardown.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from agentboard.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        try:
            s.rollback()
        except Exception:
            pass
        s.close()
        engine.dispose()


@pytest.fixture
def auth_headers(db_session_override):
    """Return (headers_dict, user) for a freshly registered normal user.

    Usage:

        def test_foo(client, auth_headers):
            headers, user = auth_headers
            r = client.get("/api/auth/me", headers=headers)
    """
    from agentboard import auth
    from agentboard.features.identity.service import register_user

    u = register_user(
        db_session_override,
        username=f"u-{uuid.uuid4().hex[:8]}",
        password="test1234",
    )
    token = auth.make_token(u.id)
    return {"Authorization": f"Bearer {token}"}, u


@pytest.fixture
def admin_headers(db_session_override):
    """Return (headers_dict, user) for a freshly registered admin user."""
    from agentboard import auth
    from agentboard.features.identity.service import register_user, set_user_admin

    u = register_user(
        db_session_override,
        username=f"admin-{uuid.uuid4().hex[:8]}",
        password="test1234",
    )
    set_user_admin(db_session_override, u.id, True)
    db_session_override.refresh(u)
    token = auth.make_token(u.id)
    return {"Authorization": f"Bearer {token}"}, u
