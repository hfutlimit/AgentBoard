"""User console integration test (Phase 4 fallout fix).

兼容处理:`agentboard` 在 conftest / 其他 test 加载时,会用默认
``sqlite:///./agentboard.db`` 实例化全局 engine(陈旧 schema,常缺 ``needs_design``
等近期加的列)。本 test 必须把全局 engine 切到临时文件 db,让 ``Base.metadata.create_all``
基于当前 metadata 建出完整 schema。切法:
1. ``tests/conftest.py`` 顶部已经把 ``AGENTBOARD_DB_URL`` 设成临时文件(兜底)。
2. 本 test 内 ``importlib.reload`` 整个 ``agentboard.core.infrastructure.database`` +
   ``agentboard.database`` 链,让 reload 后的 engine / SessionLocal 走新 URL。
3. ``monkeypatch.setattr`` 刷新所有 ``from ... import SessionLocal`` 缓存的旧引用
   (api.py / api_helpers.py / 各 feature 子模块),让 middleware ``with SessionLocal()``
   也走新 sessionlocal。
4. ``init_db()`` 全部 noop,跳过 Alembic 升级(本 test 只用 ``Base.metadata.create_all``)。
"""
import os as _os
import tempfile as _tempfile

# 在所有 agentboard import 之前先设 env
_DB_DIR = _tempfile.mkdtemp(prefix="agentboard_test_user_console_")
_DB_PATH = _os.path.join(_DB_DIR, "user_console.db")
_os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from agentboard import auth, service  # noqa: E402
from agentboard import api as api_module  # noqa: E402
from agentboard.api import app  # noqa: E402
from agentboard.models import Base  # noqa: E402


def test_user_console_profile_projects_notifications_and_api_key_scopes(monkeypatch):
    # 在 test 内显式设 env(覆盖任何先前 test 设的值),保证 reload 后
    # 重建的 global engine 走本 test 专用的临时文件。
    import tempfile as _t
    _local_db_dir = _t.mkdtemp(prefix="agentboard_uc_local_")
    db_path = _os.path.join(_local_db_dir, "user_console_local.db")
    monkeypatch.setenv("AGENTBOARD_DB_URL", f"sqlite:///{db_path}")

    # 1) Reload database 链,让全局 engine 走新 URL
    import importlib
    import agentboard.core.infrastructure.database as _infdb
    importlib.reload(_infdb)
    import agentboard.database as _facade_db
    importlib.reload(_facade_db)
    new_sessionlocal = _facade_db.SessionLocal  # reload 后的新 sessionlocal
    new_get_session = _facade_db.get_session
    new_engine = _facade_db.engine

    # 2) 刷新所有缓存了旧 SessionLocal / get_session / init_db 的模块属性
    import agentboard.api_helpers as _api_helpers
    monkeypatch.setattr(api_module, "SessionLocal", new_sessionlocal)
    monkeypatch.setattr(api_module, "get_session", new_get_session)
    monkeypatch.setattr(_api_helpers, "SessionLocal", new_sessionlocal)
    async def _noop_init_db():
        return None
    monkeypatch.setattr(api_module, "init_db", _noop_init_db)
    monkeypatch.setattr("agentboard.database.init_db", _noop_init_db)
    monkeypatch.setattr("agentboard.core.infrastructure.database.init_db", _noop_init_db)
    # 3) features/* 子模块也可能 cache 了 SessionLocal,逐个 patch
    import sys as _sys
    for mod_name, mod in list(_sys.modules.items()):
        if not mod_name.startswith("agentboard."):
            continue
        d = getattr(mod, "__dict__", None)
        if d and "SessionLocal" in d and d["SessionLocal"] is not new_sessionlocal:
            monkeypatch.setattr(mod, "SessionLocal", new_sessionlocal)

    engine = new_engine
    sessions = new_sessionlocal
    Base.metadata.create_all(engine)

    with sessions() as session:
        alice = service.register_user(session, username="console-alice", password="password123")
        bob = service.register_user(session, username="console-bob", password="password123")
        alice_id, bob_id = alice.id, bob.id

    def override_session():
        with sessions() as session:
            session.info["auto_commit"] = True
            yield session

    app.dependency_overrides[api_module.get_session] = override_session
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
            "task_assigned", "mentioned",
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
        app.dependency_overrides.pop(api_module.get_session, None)
        engine.dispose()
