"""Ticket 全局搜索端点回归护栏（Task 1039，Epic 133 v6.18 命令面板接入 Ticket 搜索）。

覆盖：
1. service.search_ticket_requests：按 title/type/关联提案标题关键词匹配、updated_at desc + id desc、
   limit、返回结构附加 project_id；
2. 可见性收敛：admin 全量；普通用户仅搜索自己 ProjectMember 项目下提案关联的工单；
3. API 端点 /api/search/tickets：200 结构、401 未鉴权、q 必填 422、limit 上限 422；
4. 路由冲突：/api/search/tickets 不被其它端点捕获；
5. 与既有搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_ticket_search.py -q
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


def _insert_ticket(s, proposal_id: int, *, title: str, type_: str = "task",
                   status: str = "done", ticket_id: int | None = 1):
    """直接插入 ProposalTicketRequest 行（绕过创建状态机校验，聚焦搜索行为）。"""
    from agentboard.domains.proposals.models import ProposalTicketRequest
    from agentboard.service import utc_now

    req = ProposalTicketRequest(
        proposal_id=proposal_id,
        type=type_,
        title=title,
        status=status,
        ticket_id=ticket_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    s.add(req)
    s.commit()
    s.refresh(req)
    return req.id


def _seed():
    """种子：admin（第一个注册自动 admin）+ 普通用户 member。

    - admin 可见全部项目；
    - member 仅成员 project_a（project_b 非成员）。
    - project_a 提案「Zebra 导入工具」挂 1 个 title='Zebra 批处理工单' 的 task 工单
      （同时用提案标题命中）；project_b 提案「Kangaroo 导出报表」挂
      title=''（默认用提案标题）的 story 工单 + 一个 type=bug 工单。
    """
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    admin_token = _auth_token(client, "tk_search_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_token = _auth_token(client, "tk_search_member")
    member_headers = {"Authorization": f"Bearer {member_token}"}

    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Ticket Search Project A", "key": "TSA"})
    assert resp.status_code in (200, 201), resp.text
    pid_a = resp.json()["id"]
    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Ticket Search Project B", "key": "TSB"})
    assert resp.status_code in (200, 201), resp.text
    pid_b = resp.json()["id"]

    resp = client.post(f"/api/projects/{pid_a}/members",
                       headers=admin_headers,
                       json={"username": "tk_search_member", "role": "member"})
    assert resp.status_code in (200, 201), resp.text

    resp = client.post("/api/proposals", headers=admin_headers, json={
        "project_id": pid_a, "title": "Zebra 导入工具", "content": "批量导入提案数据",
    })
    assert resp.status_code in (200, 201), resp.text
    prop_a = resp.json()["id"]
    resp = client.post("/api/proposals", headers=admin_headers, json={
        "project_id": pid_b, "title": "Kangaroo 导出报表", "content": "导出统计报表",
    })
    assert resp.status_code in (200, 201), resp.text
    prop_b = resp.json()["id"]

    with SessionLocal() as s:
        tk_a = _insert_ticket(s, prop_a, title="Zebra 批处理工单", type_="task", status="done")
        tk_b = _insert_ticket(s, prop_b, title="", type_="story", status="processing")
        tk_c = _insert_ticket(s, prop_b, title="Kangaroo 巡检", type_="bug", status="failed")

    return (client, admin_headers, member_headers, pid_a, pid_b,
            prop_a, prop_b, tk_a, tk_b, tk_c)


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_service_title_match(seeded):
    """工单标题关键词匹配（title 字段）。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        rows = service.search_ticket_requests(s, q="批处理", limit=10)
    assert [r["id"] for r in rows] == [tk_a]


def test_search_service_type_match(seeded):
    """工单类型关键词匹配（type 字段，如搜 bug）。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        rows = service.search_ticket_requests(s, q="bug", limit=10)
    assert [r["id"] for r in rows] == [tk_c]


def test_search_service_proposal_title_match(seeded):
    """关联提案标题匹配（工单 title 为空时靠提案标题命中）。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        rows = service.search_ticket_requests(s, q="Kangaroo", limit=10)
    # Kangaroo 同时命中提案 B 标题 → tk_b（title 空，命中提案标题）与 tk_c（标题含 Kangaroo）
    assert {r["id"] for r in rows} == {tk_b, tk_c}


def test_search_service_returns_project_id(seeded):
    """返回结构必须附加 project_id（前端 hint 显示项目名依赖）。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        rows = service.search_ticket_requests(s, q="批处理", limit=10)
    assert len(rows) == 1
    assert rows[0]["project_id"] == pid_a
    assert rows[0]["proposal_id"] == prop_a
    assert rows[0]["type"] == "task"
    assert rows[0]["status"] == "done"


def test_search_service_visibility_admin_full(seeded):
    """admin（user_id 给定时非 admin 收敛）：admin 可见全部项目的工单。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        admin_user = service.get_user_by_username(s, "tk_search_admin")
        # 「工单」命中 A 项目 tk_a；「Kangaroo」命中 B 项目 tk_b + tk_c（跨项目全量）
        rows = service.search_ticket_requests(s, q="工单", limit=50, user_id=admin_user.id)
    assert {r["id"] for r in rows} == {tk_a}
    with SessionLocal() as s:
        rows2 = service.search_ticket_requests(s, q="Kangaroo", limit=50, user_id=admin_user.id)
    assert {r["id"] for r in rows2} == {tk_b, tk_c}


def test_search_service_visibility_member_only_own_project(seeded):
    """普通用户仅搜到自己成员项目（project_a）的工单，project_b 不可见。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        member_user = service.get_user_by_username(s, "tk_search_member")
        rows = service.search_ticket_requests(s, q="工单", limit=50, user_id=member_user.id)
    ids = {r["id"] for r in rows}
    assert tk_a in ids
    assert tk_b not in ids
    assert tk_c not in ids
    # B 项目工单对 member 完全不可见
    with SessionLocal() as s:
        rows2 = service.search_ticket_requests(s, q="Kangaroo", limit=50, user_id=member_user.id)
    assert rows2 == []


def test_search_service_no_match(seeded):
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    with SessionLocal() as s:
        assert service.search_ticket_requests(s, q="不存在的关键词xyz", limit=10) == []


def test_search_tickets_api_endpoint(seeded):
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    resp = client.get("/api/search/tickets", params={"q": "批处理"}, headers=admin_h)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["id"] == tk_a
    assert data[0]["title"] == "Zebra 批处理工单"
    # 序列化契约：_ser 全列 + 附加 project_id（前端 hint 依赖 project_id/type/status）
    assert "project_id" in data[0] and data[0]["project_id"] == pid_a
    assert "type" in data[0] and "status" in data[0] and "updated_at" in data[0]


def test_search_tickets_api_visibility(seeded):
    """API 层同样按调用者收敛：member 搜不到 project_b 的工单。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    resp = client.get("/api/search/tickets", params={"q": "Kangaroo"}, headers=member_h)
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_tickets_api_requires_auth(seeded):
    """未鉴权 → 401（镜像提案搜索的隐私端点语义）。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    assert client.get("/api/search/tickets", params={"q": "批处理"}).status_code == 401


def test_search_tickets_api_q_required_and_limit_cap(seeded):
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    # q 缺省 → 422
    assert client.get("/api/search/tickets", headers=admin_h).status_code == 422
    # limit 超过 50 → 422
    assert client.get("/api/search/tickets", params={"q": "批处理", "limit": 51},
                      headers=admin_h).status_code == 422
    # limit 上限 50 合法
    assert client.get("/api/search/tickets", params={"q": "批处理", "limit": 50},
                      headers=admin_h).status_code == 200


def test_search_tickets_route_not_conflict(seeded):
    """/api/search/tickets 与提案详情路由 /api/proposals/{pid} 互不冲突（200 而非 404/422）。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    resp = client.get("/api/search/tickets", params={"q": "Kangaroo"}, headers=admin_h)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_search_tickets_endpoints_coexist(seeded):
    """/api/search/tickets 与既有 search 端点并存互不干扰。"""
    (client, admin_h, member_h, pid_a, pid_b,
     prop_a, prop_b, tk_a, tk_b, tk_c) = seeded
    assert client.get("/api/search/tickets", params={"q": "批处理"}, headers=admin_h).status_code == 200
    assert client.get("/api/search/proposals", params={"q": "Zebra"}, headers=admin_h).status_code == 200
    assert client.get("/api/search/agents", params={"q": "wb"}, headers=admin_h).status_code == 200
    assert client.get("/api/search/sprints", params={"q": "Sprint"}).status_code == 200
    assert client.get("/api/search/epics", params={"q": "Epic"}).status_code == 200
    assert client.get("/api/search/stories", params={"q": "Story"}).status_code == 200
    assert client.get("/api/search/notifications", params={"q": "任务"}, headers=admin_h).status_code == 200
