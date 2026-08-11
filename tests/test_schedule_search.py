"""定时计划（AgentSchedule）全局搜索端点回归护栏（Task 1044，Epic 134 v6.19 命令面板接入 Schedule 搜索）。

覆盖：
1. service.search_schedules：按 title/agent/schedule_type 关键词匹配、updated_at desc + id desc、limit；
2. 可见性收敛：admin 全量；普通用户仅搜索自己 ProjectMember 项目下的定时计划；
3. API 端点 /api/search/schedules：200 结构、401 未鉴权、q 必填 422、limit 上限 422；
4. 路由冲突：/api/search/schedules 不被项目内 /api/projects/{pid}/schedules 捕获；
5. 与既有搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_schedule_search.py -q
"""
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()  # 跑完整 alembic 迁移链


def _auth_token(client, username: str) -> str:
    """注册/登录用户并返回 Bearer token。"""
    resp = client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["token"]


def _seed():
    """种子：admin（第一个注册自动 admin）+ 普通用户 member。

    - admin 可见全部项目；
    - member 仅成员 project_a（project_b 非成员）。
    - project_a 下 schedule_a「夜间构建计划」绑定 dev-agent；
    - project_b 下 schedule_b「周报汇总」绑定 ops-agent、schedule_c 类型 once。
    """
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    admin_token = _auth_token(client, "sch_search_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_token = _auth_token(client, "sch_search_member")
    member_headers = {"Authorization": f"Bearer {member_token}"}

    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Schedule Search Project A", "key": "SSA"})
    assert resp.status_code in (200, 201), resp.text
    pid_a = resp.json()["id"]
    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Schedule Search Project B", "key": "SSB"})
    assert resp.status_code in (200, 201), resp.text
    pid_b = resp.json()["id"]

    resp = client.post(f"/api/projects/{pid_a}/members",
                       headers=admin_headers,
                       json={"username": "sch_search_member", "role": "member"})
    assert resp.status_code in (200, 201), resp.text

    resp = client.post(f"/api/projects/{pid_a}/schedules", headers=admin_headers, json={
        "title": "夜间构建计划", "schedule_type": "cron", "cron_expr": "0 2 * * *",
        "agent": "codex",
    })
    assert resp.status_code in (200, 201), resp.text
    sch_a = resp.json()["id"]
    resp = client.post(f"/api/projects/{pid_b}/schedules", headers=admin_headers, json={
        "title": "周报汇总计划", "schedule_type": "cron", "cron_expr": "0 18 * * 5",
        "agent": "claude",
    })
    assert resp.status_code in (200, 201), resp.text
    sch_b = resp.json()["id"]
    resp = client.post(f"/api/projects/{pid_b}/schedules", headers=admin_headers, json={
        "title": "一次性迁移计划", "schedule_type": "once", "agent": "workbuddy",
    })
    assert resp.status_code in (200, 201), resp.text
    sch_c = resp.json()["id"]

    return (client, admin_headers, member_headers,
            pid_a, pid_b, sch_a, sch_b, sch_c)


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_service_title_match(seeded):
    """计划标题关键词匹配（title 字段）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    with SessionLocal() as s:
        rows = service.search_schedules(s, q="夜间", limit=10)
    assert [r.id for r in rows] == [sch_a]


def test_search_service_agent_match(seeded):
    """绑定 Agent 关键词匹配（agent 字段）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    with SessionLocal() as s:
        rows = service.search_schedules(s, q="claude", limit=10)
    assert [r.id for r in rows] == [sch_b]


def test_search_service_type_match(seeded):
    """计划类型关键词匹配（schedule_type 字段，如搜 once）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    with SessionLocal() as s:
        rows = service.search_schedules(s, q="once", limit=10)
    assert [r.id for r in rows] == [sch_c]


def test_search_service_limit(seeded):
    """limit 截断生效。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    with SessionLocal() as s:
        rows = service.search_schedules(s, q="计划", limit=1)
    assert len(rows) == 1


def test_search_service_admin_visible_all(seeded):
    """admin（user_id=None）可见全部项目的计划。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    with SessionLocal() as s:
        rows = service.search_schedules(s, q="计划", limit=10)
    ids = {r.id for r in rows}
    assert {sch_a, sch_b, sch_c} <= ids


def test_search_service_member_visibility(seeded):
    """普通用户仅可见自己成员项目下的计划（project_a 可见、project_b 不可见）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    with SessionLocal() as s:
        uid = service.get_user_by_username(s, "sch_search_member").id
        rows = service.search_schedules(s, q="计划", limit=10, user_id=uid)
    ids = {r.id for r in rows}
    assert sch_a in ids
    assert sch_b not in ids
    assert sch_c not in ids


def test_api_search_ok_structure(seeded):
    """GET /api/search/schedules 返回 200 且结构与 _ser 对齐。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    resp = client.get("/api/search/schedules?q=构建", headers=admin_h)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    item = rows[0]
    assert item["id"] == sch_a
    assert item["title"] == "夜间构建计划"
    assert item["project_id"] == pid_a
    assert item["agent"] == "codex"
    assert item["schedule_type"] == "cron"
    assert "created_at" in item


def test_api_member_visibility(seeded):
    """API 层普通用户仅见成员项目计划。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    resp = client.get("/api/search/schedules?q=计划", headers=member_h)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert any(r["id"] == sch_a for r in rows)
    assert all(r["project_id"] == pid_a for r in rows)


def test_api_unauthorized(seeded):
    """未鉴权 401。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    resp = client.get("/api/search/schedules?q=计划")
    assert resp.status_code == 401


def test_api_missing_q(seeded):
    """q 必填 422。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    resp = client.get("/api/search/schedules", headers=admin_h)
    assert resp.status_code == 422


def test_api_limit_validation(seeded):
    """limit 越界 422。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    resp = client.get("/api/search/schedules?q=计划&limit=999", headers=admin_h)
    assert resp.status_code == 422


def test_api_route_no_shadow(seeded):
    """/api/search/schedules 不被 /api/projects/{pid}/schedules 捕获（search 非数字 pid）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    resp = client.get("/api/search/schedules?q=构建", headers=admin_h)
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == sch_a


def test_api_coexists_with_sibling_searches(seeded):
    """与既有搜索端点并存互不干扰（agents/proposals/tickets 均 200）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, sch_c) = seeded
    for path in ("/api/search/schedules?q=构建", "/api/search/agents?q=zzz",
                 "/api/search/proposals?q=zzz", "/api/search/tickets?q=zzz"):
        resp = client.get(path, headers=admin_h)
        assert resp.status_code == 200, f"{path}: {resp.status_code}"
