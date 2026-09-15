"""Regression tests for the 2026-09-14 P0 endpoint that previously raised
``TypeError`` on every request because the auth helper was called with the
wrong arity (``_auth_is_required(authorization, s)`` vs the zero-arg
``_auth_is_required() -> bool``).

What we lock down here
----------------------
1. With ``AGENTBOARD_REQUIRE_AUTH=1`` and no bearer token → ``401 unauthorized``.
2. With auth required and a non-admin bearer → ``403 admin required``.
3. With auth required and an admin bearer → ``200`` and a well-formed
   ``{"warned": [...], "taken_over": [...], "scanned_at": ...}`` payload.
4. With ``AGENTBOARD_REQUIRE_AUTH`` unset (dev mode) the endpoint stays
   callable so the maintenance worker keeps working without ceremony.

This file intentionally calls the live FastAPI app via ``TestClient`` so
the regression is at the router layer, not just inside the service helper.
"""
import os

# Force ``AGENTBOARD_REQUIRE_AUTH=1`` for the first three scenarios. The
# fourth case clears it explicitly after we capture the value.
os.environ["AGENTBOARD_REQUIRE_AUTH"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import auth, service
from agentboard.api import app
from agentboard.core.infrastructure.database import get_session


def _setup():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentboard.core.common.models import Base
    from agentboard.features.documents import models as _doc_models  # noqa: F401
    from agentboard.features.identity import models as _id_models  # noqa: F401
    from agentboard.features.projects import models as _proj_models  # noqa: F401
    from agentboard.features.scheduling import models as _sched_models  # noqa: F401
    from agentboard.features.work_items import models as _wi_models  # noqa: F401
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as s:
        admin = service.register_user(s, username="scan-admin", password="password123")
        service.set_user_admin(s, admin.id, True)
        normal = service.register_user(s, username="scan-normal", password="password123")
        admin_token = auth.make_token(admin.id)
        normal_token = auth.make_token(normal.id)

    def override_session():
        with sessions() as s:
            s.info["auto_commit"] = False
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    return client, admin_token, normal_token


def test_scan_stale_runs_requires_auth_when_auth_enabled():
    client, _admin_tok, _norm_tok = _setup()
    try:
        r = client.post("/api/scheduling/scan-stale-runs")
        assert r.status_code == 401, r.text
    finally:
        app.dependency_overrides.clear()


def test_scan_stale_runs_requires_admin_role():
    client, _admin_tok, normal_token = _setup()
    try:
        r = client.post(
            "/api/scheduling/scan-stale-runs",
            headers={"Authorization": f"Bearer {normal_token}"},
        )
        # Non-admin caller must get 403, not 200 / 500.
        assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_scan_stale_runs_admin_returns_well_formed_payload():
    client, admin_token, _normal_tok = _setup()
    try:
        r = client.post(
            "/api/scheduling/scan-stale-runs",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # The P0 regression is that this returned 500 (TypeError). Now 200.
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) >= {"warned", "taken_over", "scanned_at"}
        assert isinstance(body["warned"], list)
        assert isinstance(body["taken_over"], list)
        assert isinstance(body["scanned_at"], str)
    finally:
        app.dependency_overrides.clear()


def test_scan_stale_runs_works_in_dev_mode_without_auth():
    # Dev mode: AGENTBOARD_REQUIRE_AUTH unset → no auth, endpoint callable.
    saved = os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)
    try:
        client, _admin_tok, _normal_tok = _setup()
        try:
            r = client.post("/api/scheduling/scan-stale-runs")
            assert r.status_code == 200, r.text
            assert "warned" in r.json()
        finally:
            app.dependency_overrides.clear()
    finally:
        if saved is not None:
            os.environ["AGENTBOARD_REQUIRE_AUTH"] = saved