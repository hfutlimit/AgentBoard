from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import auth, service
from agentboard import api as api_module
from agentboard.api import app
from agentboard.database import get_session
from agentboard.models import Base


def test_user_console_profile_projects_notifications_and_api_key_scopes(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    with sessions() as session:
        alice = service.register_user(session, username="console-alice", password="password123")
        bob = service.register_user(session, username="console-bob", password="password123")
        alice_id, bob_id = alice.id, bob.id

    def override_session():
        with sessions() as session:
            session.info["auto_commit"] = True
            yield session

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(api_module, "SessionLocal", sessions)
    monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "1")
    client = TestClient(app)
    alice_headers = {"Authorization": f"Bearer {auth.make_token(alice_id)}"}
    bob_headers = {"Authorization": f"Bearer {auth.make_token(bob_id)}"}

    try:
        profile = client.patch(
            "/api/auth/me", headers=alice_headers,
            json={"display_name": "Alice A", "email": "Alice@Example.com", "avatar_url": "https://example.com/a.png"},
        )
        assert profile.status_code == 200
        assert profile.json()["display_name"] == "Alice A"
        assert profile.json()["email"] == "alice@example.com"

        changed = client.post(
            "/api/auth/change-password", headers=alice_headers,
            json={"current_password": "password123", "new_password": "new-password123"},
        )
        assert changed.status_code == 204
        with sessions() as session:
            assert service.authenticate_user(session, username="console-alice", password="new-password123")

        project = client.post(
            "/api/projects", headers=alice_headers, json={"name": "Alice Project", "key": "ALICE"},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]

        mine = client.get("/api/users/me/projects", headers=alice_headers)
        assert mine.status_code == 200
        assert mine.json()["items"][0]["membership_role"] == "owner"

        assert client.patch(
            f"/api/projects/{project_id}", headers=bob_headers, json={"name": "Stolen"},
        ).status_code == 403
        assert client.delete(f"/api/projects/{project_id}", headers=bob_headers).status_code == 403
        assert client.patch(
            f"/api/projects/{project_id}", headers=alice_headers, json={"description": "Owner edit"},
        ).status_code == 200

        epic = client.post(
            f"/api/projects/{project_id}/epics", headers=alice_headers, json={"title": "Epic"},
        ).json()
        story = client.post(
            f"/api/epics/{epic['id']}/stories", headers=alice_headers, json={"title": "Story"},
        ).json()
        task = client.post(
            f"/api/stories/{story['id']}/tasks", headers=alice_headers,
            json={"project_id": project_id, "title": "Assigned task", "assignee_id": bob_id},
        )
        assert task.status_code == 201
        task_id = task.json()["id"]
        assert client.patch(
            f"/api/tasks/{task_id}", headers=alice_headers, json={"status": "todo"},
        ).status_code == 200
        assert client.post(
            f"/api/tasks/{task_id}/comments", headers=alice_headers,
            json={"author": "console-alice", "content": "@console-bob please review"},
        ).status_code == 201

        notifications = client.get("/api/notifications", headers=bob_headers)
        assert notifications.status_code == 200
        assert {item["type"] for item in notifications.json()["items"]} >= {
            "task_assigned", "status_changed", "mentioned",
        }

        read_key = client.post(
            "/api/api-keys", headers=alice_headers,
            json={"name": "Read only", "permissions": ["api:read"]},
        ).json()["key"]
        key_headers = {"Authorization": f"Bearer {read_key}"}
        assert client.get("/api/auth/me", headers=key_headers).status_code == 200
        assert client.patch(
            "/api/auth/me", headers=key_headers, json={"display_name": "Forbidden"},
        ).status_code == 403
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()
