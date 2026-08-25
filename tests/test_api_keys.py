from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import auth, service
from agentboard.api import app
from agentboard.database import get_session
from agentboard.models import ApiKey, Base
from agentboard.features.scheduling.models import AgentRun, AgentSchedule, RunEvent
from agentboard.features.projects.models import Agent, Project
from agentboard.features.identity.models import User


def test_api_key_lifecycle_and_ownership():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as session:
        alice = service.register_user(session, username="alice-key", password="password123")
        bob = service.register_user(session, username="bob-key", password="password123")
        alice_token = auth.make_token(alice.id)
        bob_token = auth.make_token(bob.id)

    def override_session():
        with sessions() as session:
            session.info["auto_commit"] = False
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    try:
        created = client.post(
            "/api/api-keys", headers=alice_headers,
            json={"name": "Claude MCP", "permissions": ["mcp:tools:read", "mcp:tools:read", "mcp:tools:execute"]},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["key"].startswith("abk_")
        assert body["permissions"] == ["mcp:tools:execute", "mcp:tools:read"]
        key_id = body["id"]

        listed = client.get("/api/api-keys", headers=alice_headers).json()["items"]
        assert len(listed) == 1
        assert "key" not in listed[0]

        patched = client.patch(
            f"/api/api-keys/{key_id}", headers=alice_headers,
            json={"name": "Disabled MCP", "enabled": False, "permissions": ["mcp:tools:read"]},
        )
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False
        assert patched.json()["name"] == "Disabled MCP"

        assert client.get(
            f"/api/api-keys/{key_id}", headers={"Authorization": f"Bearer {bob_token}"},
        ).status_code == 404
        assert client.post(
            "/api/api-keys", headers=alice_headers,
            json={"name": "bad", "permissions": ["not namespaced"]},
        ).status_code == 422

        with sessions() as session:
            stored = session.get(ApiKey, key_id)
            assert stored.key_hash == auth.hash_api_key(body["key"])
            assert body["key"] not in stored.key_hash
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


def test_revoke_after_audit_event_preserves_audit_trail():
    """P0: revoking a key that already produced AgentRun events must
    not raise IntegrityError (the FK is now ON DELETE RESTRICT) and must
    leave the audit chain intact.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def override_session():
        with sessions() as session:
            session.info["auto_commit"] = False
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)

    with sessions() as session:
        owner = service.register_user(
            session, username="revoke-owner", password="password123",
        )
        project = Project(name="revoke-test", key="RVK", description="")
        agent = Agent(
            agent_id="revoke-agent",
            name="Revoke Agent",
            user_id=owner.id,
            roles='["developer"]',
            capabilities="[]",
            model="test",
        )
        session.add_all([project, agent])
        session.flush()
        schedule = AgentSchedule(
            project_id=project.id, title="revoke sched", schedule_type="once",
            agent=agent.agent_id,
        )
        session.add(schedule)
        session.flush()
        run = AgentRun(
            schedule_id=schedule.id, status="pending",
            agent_registry_id=agent.id, agent=agent.agent_id, model=agent.model,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        owner_id = owner.id
        run_id = run.id

    owner_token = auth.make_token(owner_id)
    headers = {"Authorization": f"Bearer {owner_token}"}

    # 1. Create the API key
    create_resp = client.post(
        "/api/api-keys", headers=headers,
        json={"name": "audit key", "permissions": ["api:read", "api:write"]},
    )
    assert create_resp.status_code == 201
    secret = create_resp.json()["key"]
    key_id = create_resp.json()["id"]

    # 2. Use the key to create an audit event (the failure mode of the bug)
    key_headers = {"Authorization": f"Bearer {secret}"}
    event_resp = client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=key_headers,
        json={"event_type": "agent.output", "payload": {"msg": "ok"}},
    )
    assert event_resp.status_code == 201, event_resp.text
    event_id = event_resp.json()["id"]
    assert event_resp.json()["api_key_id"] == key_id

    # 3. Revoke the key — used to fail with IntegrityError under
    #    ON DELETE RESTRICT; must now soft-revoke.
    revoke_resp = client.delete(f"/api/api-keys/{key_id}", headers=headers)
    assert revoke_resp.status_code == 204, revoke_resp.text

    # 4. The key row is preserved with enabled=False / revoked_at set.
    with sessions() as session:
        stored = session.get(ApiKey, key_id)
        assert stored is not None
        assert stored.enabled is False
        assert stored.revoked_at is not None
        # The audit row is still linked to the revoked key (FK intact).
        event = session.get(RunEvent, event_id)
        assert event is not None
        assert event.api_key_id == key_id

    # 5. Subsequent authentication with the revoked key is rejected.
    #    The auth path returns 401 because enabled is False.
    after = client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=key_headers,
        json={"event_type": "agent.output", "payload": {"msg": "should fail"}},
    )
    assert after.status_code == 401, after.text

    # A revoked key is terminal and cannot be resurrected through the
    # management endpoint either.
    reenable = client.patch(
        f"/api/api-keys/{key_id}",
        headers=headers,
        json={"enabled": True},
    )
    assert reenable.status_code == 422, reenable.text

    # 6. Re-revoking is idempotent (no error, no state regression).
    again = client.delete(f"/api/api-keys/{key_id}", headers=headers)
    assert again.status_code == 204, again.text
    with sessions() as session:
        stored = session.get(ApiKey, key_id)
        assert stored.enabled is False
        assert stored.revoked_at is not None

    app.dependency_overrides.pop(get_session, None)
    engine.dispose()
