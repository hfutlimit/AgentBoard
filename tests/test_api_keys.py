from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import auth, service
from agentboard.api import app
from agentboard.database import get_session
from agentboard.models import ApiKey, Base


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
