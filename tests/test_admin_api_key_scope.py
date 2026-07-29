"""回归测试：API Key 权限 = 关联用户权限（2026-07-29 修复）。

场景（复现自生产问题）：
- 管理员用户的 abk_ API Key 调 /api/admin/projects 曾被 403（parse_token 不认 API Key）。
- 修复后：admin key 可走 admin 通道；普通用户 key 一律 403。
- /api/projects：admin key 全量；普通 key 仅成员项目。
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import auth, service
from agentboard.api import app
from agentboard.database import get_session
from agentboard.models import Base


def _setup():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as s:
        admin = service.register_user(s, username="root-x", password="password123")
        service.set_user_admin(s, admin.id, True)
        normal = service.register_user(s, username="norm-x", password="password123")
        admin_token = auth.make_token(admin.id)
        normal_token = auth.make_token(normal.id)
        normal_id = normal.id

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
    return client, sessions, admin_token, normal_token, normal_id


def _make_key(client, bearer_token):
    resp = client.post(
        "/api/api-keys",
        headers={"Authorization": f"Bearer {bearer_token}"},
        json={"name": "scope-test", "permissions": ["api:*"]},
    )
    assert resp.status_code == 201
    return resp.json()["key"]


def test_api_key_identity_equals_user_identity():
    client, sessions, admin_token, normal_token, normal_id = _setup()
    try:
        # 造 3 个项目：p1/p2 无 normal 成员，p3 normal 是成员
        h_admin = {"Authorization": f"Bearer {admin_token}"}
        for i in range(1, 4):
            r = client.post(
                "/api/projects", headers=h_admin,
                json={"name": f"P{i}", "key": f"PX{i}", "description": ""},
            )
            assert r.status_code == 201, r.text
            if i == 3:
                p3 = r.json()["id"]
        with sessions() as s:
            service.add_project_member(s, project_id=p3, user_id=normal_id, role="member")
            s.commit()

        admin_key = _make_key(client, admin_token)
        normal_key = _make_key(client, normal_token)
        hk_admin = {"Authorization": f"Bearer {admin_key}"}
        hk_normal = {"Authorization": f"Bearer {normal_key}"}

        # 1) /api/projects：admin key 全量（>=3）；普通 key 仅成员项目（1 个）
        r = client.get("/api/projects", headers=hk_admin)
        assert r.status_code == 200
        assert r.json()["total"] >= 3
        r = client.get("/api/projects", headers=hk_normal)
        assert r.status_code == 200
        assert r.json()["total"] == 1

        # 2) /api/admin/projects：admin key 200 全量；普通 key 403
        r = client.get("/api/admin/projects", headers=hk_admin)
        assert r.status_code == 200, r.text
        assert r.json()["total"] >= 3
        r = client.get("/api/admin/projects", headers=hk_normal)
        assert r.status_code == 403

        # 3) /api/admin/users：同上
        assert client.get("/api/admin/users", headers=hk_admin).status_code == 200
        assert client.get("/api/admin/users", headers=hk_normal).status_code == 403

        # 4) 无凭证 → 保持历史行为 403 admin only
        assert client.get("/api/admin/projects").status_code == 403
    finally:
        app.dependency_overrides.pop(get_session, None)
