"""执行记录（AgentRun）全局搜索端点回归护栏（Task 1049，Epic 135 v6.20 命令面板接入 AgentRun 搜索）。

覆盖：
1. service.search_runs：按 status/summary/error_message 关键词匹配、join AgentSchedule 附加 project_id、
   id desc + limit；
2. 可见性收敛：admin 全量；普通用户仅搜索自己 ProjectMember 项目下的执行记录；
3. API 端点 /api/search/runs：200 结构、401 未鉴权、q 必填 422、limit 上限 422；
4. 路由冲突：/api/search/runs 不被 /api/runs/{rid} 或 /api/schedules/{sid}/runs 捕获；
5. 与既有搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_run_search.py -q
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
    - project_a 下 schedule_a「夜间构建计划」，挂 run_a（success，summary 含「构建完成」）
      与 run_b（failed，error_message 含「超时」）；
    - project_b 下 schedule_b「周报汇总计划」，挂 run_c（running，summary 含「分析中」）。
    """
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    admin_token = _auth_token(client, "run_search_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_token = _auth_token(client, "run_search_member")
    member_headers = {"Authorization": f"Bearer {member_token}"}

    # create_run deliberately refuses to synthesize offline Agent rows.  Keep
    # this integration fixture aligned with the real dispatch precondition by
    # registering each Agent referenced by the schedules below.
    for agent_id in ("codex", "claude"):
        resp = client.post("/api/agents/register", headers=admin_headers, json={
            "agent_id": agent_id,
            "name": f"Run Search {agent_id.title()}",
            "roles": '["developer"]',
            "capabilities": "[]",
            "cli_command": "echo ready",
        })
        assert resp.status_code in (200, 201), resp.text

    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Run Search Project A", "key": "RSA"})
    assert resp.status_code in (200, 201), resp.text
    pid_a = resp.json()["id"]
    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Run Search Project B", "key": "RSB"})
    assert resp.status_code in (200, 201), resp.text
    pid_b = resp.json()["id"]

    resp = client.post(f"/api/projects/{pid_a}/members",
                       headers=admin_headers,
                       json={"username": "run_search_member", "role": "member"})
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

    # run_a：success + summary（project_a）
    resp = client.post(f"/api/schedules/{sch_a}/runs", headers=admin_headers, json={})
    assert resp.status_code == 201, resp.text
    run_a = resp.json()["id"]
    resp = client.patch(f"/api/runs/{run_a}", headers=admin_headers,
                        json={"status": "success", "summary": "构建完成，产物已上传"})
    assert resp.status_code == 200, resp.text

    # run_b：failed + error_message（project_a）
    resp = client.post(f"/api/schedules/{sch_a}/runs", headers=admin_headers, json={})
    assert resp.status_code == 201, resp.text
    run_b = resp.json()["id"]
    resp = client.patch(f"/api/runs/{run_b}", headers=admin_headers,
                        json={"status": "failed", "error_message": "构建超时（120s 上限）"})
    assert resp.status_code == 200, resp.text

    # run_c：running + summary（project_b）
    resp = client.post(f"/api/schedules/{sch_b}/runs", headers=admin_headers, json={})
    assert resp.status_code == 201, resp.text
    run_c = resp.json()["id"]
    resp = client.patch(f"/api/runs/{run_c}", headers=admin_headers,
                        json={"status": "running", "summary": "数据分析进行中"})
    assert resp.status_code == 200, resp.text

    return (client, admin_headers, member_headers,
            pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c)


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_service_status_match(seeded):
    """运行状态关键词匹配（status 字段，如搜 failed）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        rows = service.search_runs(s, q="failed", limit=10)
    assert [r["id"] for r in rows] == [run_b]


def test_search_service_summary_match(seeded):
    """执行摘要关键词匹配（summary 字段）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        rows = service.search_runs(s, q="构建完成", limit=10)
    assert [r["id"] for r in rows] == [run_a]


def test_search_service_error_message_match(seeded):
    """错误信息关键词匹配（error_message 字段）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        rows = service.search_runs(s, q="超时", limit=10)
    assert [r["id"] for r in rows] == [run_b]


def test_search_service_project_id_attached(seeded):
    """join AgentSchedule 反查的 project_id 正确附加（run_a 属 project_a）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        rows = service.search_runs(s, q="构建完成", limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == run_a
    assert rows[0]["project_id"] == pid_a
    assert rows[0]["summary"] == "构建完成，产物已上传"


def test_search_service_limit(seeded):
    """limit 截断生效。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        rows = service.search_runs(s, q="构", limit=1)
    assert len(rows) == 1


def test_search_service_admin_visible_all(seeded):
    """admin（user_id=None）可见全部项目的执行记录。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        rows = service.search_runs(s, q="建", limit=10)
    ids = {r["id"] for r in rows}
    # run_a(构建) + run_b(构建超时) 同属 project_a
    assert run_a in ids
    assert run_b in ids


def test_search_service_member_visibility(seeded):
    """普通用户仅可见自己成员项目下的执行记录（project_a 可见、project_b 不可见）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    with SessionLocal() as s:
        uid = service.get_user_by_username(s, "run_search_member").id
        rows = service.search_runs(s, q="完成", limit=10, user_id=uid)
    assert [r["id"] for r in rows] == [run_a]
    # project_b 的 run_c 不可见：按 running 搜，member 应为空
    rows_b = service.search_runs(s, q="running", limit=10, user_id=uid)
    assert rows_b == []


def test_api_search_ok_structure(seeded):
    """GET /api/search/runs 返回 200 且结构含 _ser 全列 + project_id。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    resp = client.get("/api/search/runs?q=构建", headers=admin_h)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2  # run_a(summary) + run_b(error_message) 均含「构建」
    item = next(r for r in rows if r["id"] == run_a)
    assert item["summary"] == "构建完成，产物已上传"
    assert item["status"] == "success"
    assert item["project_id"] == pid_a
    assert "created_at" in item


def test_api_member_visibility(seeded):
    """API 层普通用户仅见成员项目的执行记录（project_b 的 run_c 不可见）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    resp = client.get("/api/search/runs?q=分析", headers=member_h)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert all(r["project_id"] == pid_a for r in rows)
    assert all(r["id"] != run_c for r in rows)


def test_api_unauthorized(seeded):
    """未鉴权 401。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    resp = client.get("/api/search/runs?q=构建")
    assert resp.status_code == 401


def test_api_missing_q(seeded):
    """q 必填 422。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    resp = client.get("/api/search/runs", headers=admin_h)
    assert resp.status_code == 422


def test_api_limit_validation(seeded):
    """limit 越界 422。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    resp = client.get("/api/search/runs?q=构建&limit=999", headers=admin_h)
    assert resp.status_code == 422


def test_api_route_no_shadow(seeded):
    """/api/search/runs 不被 /api/runs/{rid} 或 /api/schedules/{sid}/runs 捕获（search 非数字）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    resp = client.get("/api/search/runs?q=构建", headers=admin_h)
    assert resp.status_code == 200
    resp2 = client.get(f"/api/runs/{run_a}", headers=admin_h)
    assert resp2.status_code == 200
    assert resp2.json()["id"] == run_a


def test_api_coexists_with_sibling_searches(seeded):
    """与既有搜索端点并存互不干扰（schedules/agents/proposals/tickets 均 200）。"""
    (client, admin_h, member_h, pid_a, pid_b, sch_a, sch_b, run_a, run_b, run_c) = seeded
    for path in ("/api/search/runs?q=构建", "/api/search/schedules?q=构建",
                 "/api/search/agents?q=zzz", "/api/search/proposals?q=zzz",
                 "/api/search/tickets?q=zzz"):
        resp = client.get(path, headers=admin_h)
        assert resp.status_code == 200, f"{path}: {resp.status_code}"
