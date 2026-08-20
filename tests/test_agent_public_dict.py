"""Epic 151 / Story 326 / Task 1297：MembersTab 数据边界（后端契约）。

验证：
1. ``Agent.to_public_dict()`` 字段收窄：不返回 ``cli_command`` / ``auth_key`` /
   ``probe_message`` / ``user_id``，保留公开字段。
2. ``GET /api/agents`` 端点用 ``to_public_dict`` 返回（dev 模式无 token 200，
   响应不含敏感字段）。
3. ``list_agents`` service 支持 ``order_by_created`` 按 ``created_at`` 倒序。
4. ``AGENTBOARD_REQUIRE_AUTH=1`` 模拟：未登录访问 ``/api/agents`` 返回 401。

2026-08-20 创建。
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _setup_app(require_auth: bool = False):
    """Build an isolated in-memory FastAPI client."""
    os.environ["AGENTBOARD_REQUIRE_AUTH"] = "1" if require_auth else "0"
    # 清掉缓存以让 settings 重读 env
    from agentboard.core import config as cfg_mod
    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

    from agentboard.api import app
    from agentboard import service
    from agentboard.database import get_session
    from agentboard.models import Base

    # 清掉上一次测试残留的 dependency_overrides（StaticPool 跨测试不共享）
    app.dependency_overrides.clear()

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as s:
        # 创建 1 个 admin + 1 个普通用户，造 1 个 agent
        admin = service.register_user(s, username="root", password="password123")
        service.set_user_admin(s, admin.id, True)
        agent = service.register_agent(
            s, agent_id="wb-dev-1", name="Test Bot",
            roles='["reviewer"]', capabilities="[]",
            cli_command="codebuddy --model {model}",  # 含敏感模板但不是 shell 注入
            model="hy3", auth_key="abk_secretfingerprint",  # noqa: S105 — test fixture
            user_id=admin.id,
        )
        # commit 前 cache 关键 id（commit 后 instance expire，访问触发 detached lazy load）
        admin_id_cached = admin.id
        agent_id_pk = agent.id
        s.commit()

    def _override():
        with sessions() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    return TestClient(app), sessions, agent_id_pk, admin_id_cached


def _teardown_app():
    """测试结束后清掉 app.dependency_overrides，避免影响其他测试。"""
    from agentboard.api import app
    app.dependency_overrides.clear()


def test_to_public_dict_strips_sensitive_fields():
    """Agent.to_public_dict 不返回 cli_command/auth_key/probe_message/user_id。"""
    os.environ["AGENTBOARD_REQUIRE_AUTH"] = "0"
    from agentboard.core import config as cfg_mod
    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

    from agentboard.features.projects.models import Agent
    from agentboard.core.service_helpers import _ser

    # 模拟一个 ORM 对象（绕开 DB）
    import datetime as _dt

    a = Agent(
        id=1, agent_id="wb-dev-1", name="Test Bot",
        roles='["reviewer"]', capabilities="[]",
        cli_command="codebuddy --model {model}",  # noqa: S105 — test fixture
        model="hy3", auth_key="abk_secretfingerprint",  # noqa: S105
        user_id=42, online=True, enabled=True,
        last_heartbeat=_dt.datetime(2026, 8, 20, 10, 0, 0),
        probe_message="PROBE_INTERNAL_DETAIL_OK_v1.2.3",
        last_probe_at=_dt.datetime(2026, 8, 20, 9, 0, 0),
    )
    _teardown_app()
    full = _ser(a)
    public = a.to_public_dict()

    # 1) 公开字段保留
    assert public["id"] == 1
    assert public["agent_id"] == "wb-dev-1"
    assert public["name"] == "Test Bot"
    assert public["model"] == "hy3"
    assert public["online"] is True
    assert public["enabled"] is True

    # 2) 敏感字段剥离
    for forbidden in ("cli_command", "auth_key", "probe_message", "user_id"):
        assert forbidden not in public, (
            f"to_public_dict leaked {forbidden}: {public.get(forbidden)!r}"
        )
        # 同时确认 full 里确实有这个字段（防 fixture 漏）
        assert forbidden in full, (
            f"sanity: _ser should still return {forbidden}"
        )

    # 3) 公开字段集合与 Agent._PUBLIC_FIELDS 一致
    assert set(public.keys()) == set(Agent._PUBLIC_FIELDS)


def test_list_agents_endpoint_returns_public_dict():
    """GET /api/agents 响应不含敏感字段（dev 模式无 token 也 200）。"""
    client, _sessions, _aid, _uid = _setup_app(require_auth=False)
    try:
        r = client.get("/api/agents")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        row = rows[0]
        # 敏感字段
        for forbidden in ("cli_command", "auth_key", "probe_message", "user_id"):
            assert forbidden not in row, f"endpoint leaked {forbidden}: {row.get(forbidden)!r}"
        # 公开字段
        assert row["agent_id"] == "wb-dev-1"
        assert row["name"] == "Test Bot"
        assert row["model"] == "hy3"
    finally:
        _teardown_app()


def test_list_agents_endpoint_requires_auth_when_flagged():
    """REQUIRE_AUTH=1 时未登录 401；带 token 200。"""
    from agentboard import auth

    client, _sessions, _aid, admin_id = _setup_app(require_auth=False)
    try:
        token = auth.make_token(admin_id)

        # 切到 require_auth=1
        os.environ["AGENTBOARD_REQUIRE_AUTH"] = "1"
        from agentboard.core import config as cfg_mod
        cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

        # 注意：当前 router 的鉴权走 ``_auth_is_required()`` 实时读 env，
        # 而不是 settings cache，所以即便 app 已建也能感知。
        r = client.get("/api/agents")
        assert r.status_code == 401, r.text
        r = client.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text

        # 恢复
        os.environ["AGENTBOARD_REQUIRE_AUTH"] = "0"
        cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]
    finally:
        _teardown_app()


def test_list_agents_service_order_by_created_desc():
    """service.list_agents(order_by_created=True) 按 created_at 倒序。"""
    import datetime as _dt

    from agentboard.features.scheduling import service
    from agentboard.features.projects.models import Agent

    os.environ["AGENTBOARD_REQUIRE_AUTH"] = "0"
    from agentboard.core import config as cfg_mod
    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

    from agentboard import service as root_service
    from agentboard.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    s = sessions()
    admin = root_service.register_user(s, username="ord", password="password123")
    # 显式不同 created_at（用 sleep 不可靠；直接 row.created_at 覆写）
    a1 = root_service.register_agent(s, agent_id="first", name="A1", user_id=admin.id)
    a2 = root_service.register_agent(s, agent_id="second", name="A2", user_id=admin.id)
    a3 = root_service.register_agent(s, agent_id="third", name="A3", user_id=admin.id)
    # 反向设置 created_at：first 最早，third 最新
    a1.created_at = _dt.datetime(2026, 1, 1)
    a2.created_at = _dt.datetime(2026, 6, 1)
    a3.created_at = _dt.datetime(2026, 12, 1)
    s.commit()

    rows_default = service.list_agents(s)
    rows_ordered = service.list_agents(s, order_by_created=True)
    # 默认按 id 倒序（后建先出）：a3, a2, a1
    assert [r.agent_id for r in rows_default] == ["third", "second", "first"]
    # order_by_created=True 按 created_at 倒序：a3, a2, a1 — 同序
    assert [r.agent_id for r in rows_ordered] == ["third", "second", "first"]

    s.close()
    engine.dispose()


# ============================================================
# 2026-08-20 Epic 151 / Task 1297a — 5 个写接口字段脱敏
# ============================================================
#
# 验证：
# 1. register_agent（POST /api/agents/register）：
#    - admin caller → to_admin_dict（含 cli_command / auth_key / user_id）
#    - 普通用户 caller → to_public_dict（脱敏）
# 2. update_agent（PUT /api/agents/{id}）：
#    - admin / owner → to_admin_dict；其他用户 → to_public_dict
# 3. agent_heartbeat（POST /api/agents/{id}/heartbeat）：
#    - Agent 自调用 → to_public_dict
# 4. agent_deregister（POST /api/agents/{id}/deregister）：
#    - admin → to_admin_dict（看 probe_message 原因）；Agent 自己 → to_public_dict
# 5. probe_agent（POST /api/agents/{id}/probe）：
#    - admin → to_admin_dict；普通用户 → to_public_dict
# 6. delete_agent（DELETE /api/agents/{id}）：不变（返 {ok: True}）
# ============================================================


def _setup_app_two_users(require_auth: bool = False):
    """建 1 admin + 1 普通用户 + 1 个 agent（admin 注册）。返回 (client, sessions, admin_id, user_id, agent_id_pk)"""
    os.environ["AGENTBOARD_REQUIRE_AUTH"] = "1" if require_auth else "0"
    from agentboard.core import config as cfg_mod
    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

    from agentboard.api import app
    from agentboard import service
    from agentboard.database import get_session
    from agentboard.models import Base

    app.dependency_overrides.clear()

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as s:
        admin = service.register_user(s, username="admin", password="password123")
        service.set_user_admin(s, admin.id, True)
        normal = service.register_user(s, username="alice", password="password123")
        agent = service.register_agent(
            s, agent_id="wb-1", name="Test Bot",
            roles='["reviewer"]', capabilities="[]",
            cli_command="codebuddy --model {model}",  # noqa: S105 — test fixture
            model="hy3", auth_key="abk_secretfingerprint",  # noqa: S105
            user_id=admin.id,  # 归属 admin
        )
        admin_id_cached = admin.id
        user_id_cached = normal.id
        agent_id_pk = agent.id
        s.commit()

    def _override():
        with sessions() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    return TestClient(app), sessions, admin_id_cached, user_id_cached, agent_id_pk


def _auth(uid: int) -> dict:
    from agentboard import auth
    return {"Authorization": f"Bearer {auth.make_token(uid)}"}


def test_register_admin_sees_full_fields():
    """register_agent：admin caller 看到 cli_command / auth_key / user_id。"""
    client, _s, admin_id, _uid, _aid = _setup_app_two_users(require_auth=False)
    try:
        r = client.post(
            "/api/agents/register",
            json={
                "agent_id": "new-agent-admin",
                "name": "New Agent",
                "roles": '["reviewer"]',
                "capabilities": "[]",
                "cli_command": "codebuddy --model {model}",  # noqa: S106 — test fixture
                "model": "hy3",
                "auth_key": "abk_newfingerprint",  # noqa: S106
            },
            headers=_auth(admin_id),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # admin 能看到敏感字段
        assert body["cli_command"] == "codebuddy --model {model}"
        assert body["auth_key"] == "abk_newfingerprint"
        assert body["user_id"] == admin_id
    finally:
        _teardown_app()


def test_register_normal_user_sees_public_only():
    """register_agent：普通用户 caller 看到 to_public_dict（无 cli_command/auth_key/user_id）。"""
    client, _s, _aid, normal_id, _aid2 = _setup_app_two_users(require_auth=False)
    try:
        r = client.post(
            "/api/agents/register",
            json={
                "agent_id": "new-agent-normal",
                "name": "New Agent",
                "roles": '["reviewer"]',
                "capabilities": "[]",
                "cli_command": "codebuddy --model {model}",  # noqa: S106 — test fixture
                "model": "hy3",
                "auth_key": "abk_newfingerprint",  # noqa: S106
            },
            headers=_auth(normal_id),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        for forbidden in ("cli_command", "auth_key", "probe_message", "user_id"):
            assert forbidden not in body, f"register normal user leaked {forbidden}: {body.get(forbidden)!r}"
    finally:
        _teardown_app()


def test_update_admin_sees_full_fields():
    """update_agent：admin caller 看 to_admin_dict。"""
    client, _s, admin_id, _uid, _aid = _setup_app_two_users(require_auth=False)
    try:
        r = client.put(
            "/api/agents/wb-1",
            json={"name": "Renamed"},
            headers=_auth(admin_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cli_command"] == "codebuddy --model {model}"
        assert body["auth_key"] == "abk_secretfingerprint"
        assert body["user_id"] == admin_id
    finally:
        _teardown_app()


def test_update_other_user_sees_public_only():
    """update_agent：非 owner 非 admin → 403 拒绝（连写都不行）。"""
    client, _s, _aid, normal_id, _aid2 = _setup_app_two_users(require_auth=False)
    try:
        r = client.put(
            "/api/agents/wb-1",  # 归属 admin
            json={"name": "Renamed"},
            headers=_auth(normal_id),
        )
        assert r.status_code == 403, r.text
    finally:
        _teardown_app()


def test_heartbeat_returns_public_only():
    """agent_heartbeat：永远 to_public_dict（Agent 自己不需要看自己的敏感字段）。"""
    client, _s, admin_id, _uid, _aid = _setup_app_two_users(require_auth=False)
    try:
        # 模拟 Agent 自己调（用任何合法 user 身份即可）
        r = client.post(
            "/api/agents/wb-1/heartbeat",
            json={"probe_ok": True, "probe_message": "internal OK"},
            headers=_auth(admin_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        for forbidden in ("cli_command", "auth_key", "user_id"):
            assert forbidden not in body, f"heartbeat leaked {forbidden}"
    finally:
        _teardown_app()


def test_deregister_admin_sees_probe_message():
    """agent_deregister：admin caller 看 to_admin_dict（含 probe_message）。"""
    client, _s, admin_id, _uid, _aid = _setup_app_two_users(require_auth=False)
    try:
        r = client.post(
            "/api/agents/wb-1/deregister",
            json={"probe_message": "agent shutdown"},
            headers=_auth(admin_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # admin 看到完整
        assert body["cli_command"] == "codebuddy --model {model}"
        assert body["auth_key"] == "abk_secretfingerprint"
        assert body["probe_message"] == "agent shutdown"
    finally:
        _teardown_app()


def test_probe_admin_sees_cli_command():
    """probe_agent：admin caller 看 to_admin_dict（看 cli_command 详情）。"""
    client, _s, admin_id, _uid, _aid = _setup_app_two_users(require_auth=False)
    try:
        r = client.post(
            "/api/agents/wb-1/probe",
            json={"timeout": 1},
            headers=_auth(admin_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cli_command"] == "codebuddy --model {model}"
        assert body["auth_key"] == "abk_secretfingerprint"
    finally:
        _teardown_app()


def test_probe_requires_auth_even_in_dev():
    """probe_agent 永远要求鉴权（B-A2 + Task 1297a 仍保留强鉴权）。"""
    client, _s, _aid, _uid, _aid2 = _setup_app_two_users(require_auth=False)
    try:
        r = client.post("/api/agents/wb-1/probe", json={"timeout": 1})
        assert r.status_code == 401, r.text
    finally:
        _teardown_app()


def test_delete_returns_ok():
    """delete_agent：返 {ok: True}，不返 agent 字段（无脱敏需求）。"""
    client, _s, admin_id, _uid, _aid = _setup_app_two_users(require_auth=False)
    try:
        r = client.delete("/api/agents/wb-1", headers=_auth(admin_id))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"ok": True}
    finally:
        _teardown_app()
