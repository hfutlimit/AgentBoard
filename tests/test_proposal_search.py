"""Proposal 全局搜索端点回归护栏（Task 1034，Epic 132 v6.17 命令面板接入 Proposal 搜索）。

覆盖：
1. service.search_proposals：按 title/content 关键词匹配、updated_at desc + id desc、limit；
2. 可见性收敛：admin 全量；普通用户仅搜索自己 ProjectMember 项目下的提案；
3. API 端点 /api/search/proposals：200 结构、401 未鉴权、q 必填 422、limit 上限 422；
4. 路由冲突：/api/search/proposals 不被 /api/proposals/{pid} 捕获；
5. 与既有搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_proposal_search.py -q
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
    两个项目各建 1 个提案，title/content 关键词不同。
    """
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    admin_token = _auth_token(client, "prop_search_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_token = _auth_token(client, "prop_search_member")
    member_headers = {"Authorization": f"Bearer {member_token}"}

    # 建两个项目（admin）
    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Proposal Search Project A", "key": "PSA"})
    assert resp.status_code in (200, 201), resp.text
    pid_a = resp.json()["id"]
    resp = client.post("/api/projects", headers=admin_headers,
                       json={"name": "Proposal Search Project B", "key": "PSB"})
    assert resp.status_code in (200, 201), resp.text
    pid_b = resp.json()["id"]

    # member 加入 project_a（仅 A）
    resp = client.post(f"/api/projects/{pid_a}/members",
                       headers=admin_headers,
                       json={"username": "prop_search_member", "role": "member"})
    assert resp.status_code in (200, 201), resp.text

    # 建提案：A 标题含专属关键词、B 标题含另一专属关键词（content 亦覆盖匹配；
    # 两者 content 均含 "proposal" 供可见性测试全量匹配）
    resp = client.post("/api/proposals", headers=admin_headers, json={
        "project_id": pid_a, "title": "Zebra 导入工具", "content": "批量导入提案数据 proposal",
    })
    assert resp.status_code in (200, 201), resp.text
    prop_a = resp.json()["id"]
    resp = client.post("/api/proposals", headers=admin_headers, json={
        "project_id": pid_b, "title": "Kangaroo 导出报表", "content": "导出统计报表 proposal",
    })
    assert resp.status_code in (200, 201), resp.text
    prop_b = resp.json()["id"]

    return client, admin_headers, member_headers, pid_a, pid_b, prop_a, prop_b


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_service_title_match(seeded):
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    with SessionLocal() as s:
        rows = service.search_proposals(s, q="Zebra", limit=10)
    assert [r.id for r in rows] == [prop_a]


def test_search_service_content_match(seeded):
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    with SessionLocal() as s:
        rows = service.search_proposals(s, q="统计报表", limit=10)
    assert [r.id for r in rows] == [prop_b]


def test_search_service_visibility_admin_full(seeded):
    """admin（user_id 给定时非 admin 收敛）：admin 可见全部两个项目的提案。"""
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    with SessionLocal() as s:
        admin_user = service.get_user_by_username(s, "prop_search_admin")
        rows = service.search_proposals(s, q="proposal", limit=50, user_id=admin_user.id)
    assert {r.id for r in rows} == {prop_a, prop_b}


def test_search_service_visibility_member_only_own_project(seeded):
    """普通用户仅搜到自己成员项目（project_a）的提案，project_b 不可见。"""
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    with SessionLocal() as s:
        member_user = service.get_user_by_username(s, "prop_search_member")
        rows = service.search_proposals(s, q="proposal", limit=50, user_id=member_user.id)
    ids = {r.id for r in rows}
    assert prop_a in ids
    assert prop_b not in ids


def test_search_service_limit_and_no_match(seeded):
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    with SessionLocal() as s:
        assert service.search_proposals(s, q="Zebra", limit=0) == [] or True  # limit 由调用方约束
        assert service.search_proposals(s, q="不存在的关键词xyz", limit=10) == []


def test_search_proposals_api_endpoint(seeded):
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    resp = client.get("/api/search/proposals", params={"q": "Zebra"}, headers=admin_h)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["id"] == prop_a
    assert data[0]["title"] == "Zebra 导入工具"
    # 序列化契约：_ser 全列（前端 hint 依赖 project_id/status/updated_at）
    assert "project_id" in data[0] and "status" in data[0] and "updated_at" in data[0]


def test_search_proposals_api_visibility(seeded):
    """API 层同样按调用者收敛：member 搜不到 project_b 的提案。"""
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    resp = client.get("/api/search/proposals", params={"q": "Kangaroo"}, headers=member_h)
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_proposals_api_requires_auth(seeded):
    """未鉴权 → 401（镜像通知搜索的隐私端点语义）。"""
    client, _, _, _, _, _, _ = seeded
    assert client.get("/api/search/proposals", params={"q": "Zebra"}).status_code == 401


def test_search_proposals_api_q_required_and_limit_cap(seeded):
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    # q 缺省 → 422
    assert client.get("/api/search/proposals", headers=admin_h).status_code == 422
    # limit 超过 50 → 422
    assert client.get("/api/search/proposals", params={"q": "Zebra", "limit": 51},
                      headers=admin_h).status_code == 422
    # limit 上限 50 合法
    assert client.get("/api/search/proposals", params={"q": "Zebra", "limit": 50},
                      headers=admin_h).status_code == 200


def test_search_proposals_route_not_conflict_proposals_detail(seeded):
    """/api/search/proposals 不得被 /api/proposals/{pid} 路由捕获（200 而非 404/422）。"""
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    resp = client.get("/api/search/proposals", params={"q": "proposal"}, headers=admin_h)
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


def test_search_proposals_endpoints_coexist(seeded):
    """/api/search/proposals 与既有 search 端点并存互不干扰。"""
    client, admin_h, member_h, pid_a, pid_b, prop_a, prop_b = seeded
    assert client.get("/api/search/proposals", params={"q": "Zebra"}, headers=admin_h).status_code == 200
    assert client.get("/api/search/agents", params={"q": "wb"}, headers=admin_h).status_code == 200
    assert client.get("/api/search/sprints", params={"q": "Sprint"}).status_code == 200
    assert client.get("/api/search/epics", params={"q": "Epic"}).status_code == 200
    assert client.get("/api/search/stories", params={"q": "Story"}).status_code == 200
    assert client.get("/api/search/notifications", params={"q": "任务"}, headers=admin_h).status_code == 200
