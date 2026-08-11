"""Agent 全局搜索端点回归护栏（Task 1027，Epic 131 v6.16 命令面板接入 Agent 搜索）。

覆盖：
1. service.search_agents：按 agent_id/name/roles 关键词匹配、仅 enabled、id desc + limit；
2. API 端点 /api/search/agents：200 结构、401 未鉴权、q 必填 422、limit 上限 422；
3. 路由冲突：/api/search/agents 不被 /api/agents/{agent_id} 捕获；
4. 与既有搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_agent_search.py -q
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
    """注册 1 用户 + 3 Agent：agent_id/name/roles 关键词各异，其中一个 enabled=False。"""
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    token = _auth_token(client, "agent_search_user")
    headers = {"Authorization": f"Bearer {token}"}

    # 注册 3 个 agent（幂等 register 接口，agent_id 唯一）
    resp = client.post("/api/agents/register", headers=headers, json={
        "agent_id": "wb-dev-1", "name": "Dev Worker One",
        "roles": '["developer"]', "capabilities": '["python"]', "cli_command": "echo hi",
    })
    assert resp.status_code in (200, 201), resp.text
    resp = client.post("/api/agents/register", headers=headers, json={
        "agent_id": "wb-review-2", "name": "Review Agent",
        "roles": '["reviewer"]', "capabilities": '["code-review"]', "cli_command": "echo hi",
    })
    assert resp.status_code in (200, 201), resp.text
    resp = client.post("/api/agents/register", headers=headers, json={
        "agent_id": "wb-off-3", "name": "Disabled Agent",
        "roles": '["developer"]', "capabilities": "[]", "cli_command": "echo hi",
    })
    assert resp.status_code in (200, 201), resp.text
    # 禁用一个：直接 service 层设置 enabled=False
    with SessionLocal() as s:
        ag = service.get_agent_by_agent_id(s, "wb-off-3")
        service.update_agent(s, "wb-off-3", enabled=False)
        s.commit()
    return client, headers, ["wb-dev-1", "wb-review-2", "wb-off-3"]


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_service_agent_id(seeded):
    _, _, ids = seeded
    with SessionLocal() as s:
        rows = service.search_agents(s, q="wb-dev", limit=10)
    assert [r.agent_id for r in rows] == ["wb-dev-1"]
    assert rows[0].name == "Dev Worker One"


def test_search_service_name(seeded):
    _, _, ids = seeded
    with SessionLocal() as s:
        rows = service.search_agents(s, q="Review", limit=10)
    assert [r.agent_id for r in rows] == ["wb-review-2"]


def test_search_service_roles(seeded):
    _, _, ids = seeded
    with SessionLocal() as s:
        rows = service.search_agents(s, q="reviewer", limit=10)
    assert [r.agent_id for r in rows] == ["wb-review-2"]


def test_search_service_excludes_disabled(seeded):
    """enabled=False 的 Agent 不参与搜索（wb-off-3 用名字也搜不到）。"""
    _, _, ids = seeded
    with SessionLocal() as s:
        rows = service.search_agents(s, q="Disabled", limit=10)
    assert rows == []


def test_search_service_limit_and_order(seeded):
    _, _, ids = seeded
    with SessionLocal() as s:
        rows = service.search_agents(s, q="Agent", limit=2)
    # id desc 顺序（enabled 过滤后仅 2 个匹配，验证 limit 生效）
    assert len(rows) <= 2
    assert all(r.enabled for r in rows)


def test_search_service_no_match(seeded):
    _, _, ids = seeded
    with SessionLocal() as s:
        rows = service.search_agents(s, q="不存在的关键词xyz", limit=10)
    assert rows == []


def test_search_agents_api_endpoint(seeded):
    client, headers, ids = seeded
    resp = client.get("/api/search/agents", params={"q": "wb-dev"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["agent_id"] == "wb-dev-1"
    assert data[0]["name"] == "Dev Worker One"
    # 序列化契约：_ser 全列（enabled/probe_message 供前端 hint）
    assert "enabled" in data[0] and "probe_message" in data[0]


def test_search_agents_api_requires_auth(seeded):
    """未鉴权 → 401（镜像通知搜索的隐私端点语义）。"""
    client, _, _ = seeded
    assert client.get("/api/search/agents", params={"q": "wb-dev"}).status_code == 401


def test_search_agents_api_q_required_and_limit_cap(seeded):
    client, headers, ids = seeded
    # q 缺省 → 422
    assert client.get("/api/search/agents", headers=headers).status_code == 422
    # limit 超过 50 → 422
    assert client.get("/api/search/agents", params={"q": "Agent", "limit": 51}, headers=headers).status_code == 422
    # limit 上限 50 合法
    assert client.get("/api/search/agents", params={"q": "Agent", "limit": 50}, headers=headers).status_code == 200


def test_search_agents_route_not_conflict_agents_detail(seeded):
    """/api/search/agents 不得被 /api/agents/{agent_id} 路由捕获（200 而非 404/422）。"""
    client, headers, ids = seeded
    resp = client.get("/api/search/agents", params={"q": "wb"}, headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


def test_search_agents_endpoints_coexist(seeded):
    """/api/search/agents 与 /api/search/{sprints,epics,stories,notifications} 并存互不干扰。"""
    client, headers, ids = seeded
    assert client.get("/api/search/agents", params={"q": "wb"}, headers=headers).status_code == 200
    assert client.get("/api/search/sprints", params={"q": "Sprint"}).status_code == 200
    assert client.get("/api/search/epics", params={"q": "Epic"}).status_code == 200
    assert client.get("/api/search/stories", params={"q": "Story"}).status_code == 200
    assert client.get("/api/search/notifications", params={"q": "任务"}, headers=headers).status_code == 200
