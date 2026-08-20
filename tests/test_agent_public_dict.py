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
