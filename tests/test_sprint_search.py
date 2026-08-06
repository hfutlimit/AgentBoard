"""Sprint 全局搜索端点回归护栏（Task 1000，Epic 120 v6.14 命令面板接入 Sprint 搜索）。

覆盖：
1. service.search_sprints：按 title/goal 关键词匹配，id desc + limit；
2. API 端点 /api/search/sprints：200 结构、limit 上限 50、q 必填；
3. 路由冲突：/api/search/sprints 不被 /api/projects/{pid}/sprints 项目级路由拦截（200 而非 404/422）；
4. 与 Epic/Story 搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_sprint_search.py -q
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


def _seed():
    """创建 1 项目 × 3 Sprint：title/goal 关键词各不相同，便于精确匹配。"""
    with SessionLocal() as s:
        p = service.create_project(s, name="SearchSprint P")
        rows = [
            ("Sprint Alpha 迭代发布", "goal alpha 交付"),
            ("Sprint Beta 看板冲刺", "goal beta 冲刺"),
            ("Sprint Gamma 性能优化", "goal gamma 性能"),
        ]
        ids = []
        for title, goal in rows:
            sp = service.create_sprint(s, project_id=p.id, title=title, goal=goal)
            ids.append(sp.id)
        s.commit()
        return p.id, ids


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_sprints_service_title(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_sprints(s, q="Alpha", limit=10)
    assert [r.id for r in rows] == [ids[0]]
    assert rows[0].title == "Sprint Alpha 迭代发布"


def test_search_sprints_service_goal(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_sprints(s, q="性能", limit=10)
    assert [r.id for r in rows] == [ids[2]]


def test_search_sprints_service_limit_and_order(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_sprints(s, q="Sprint", limit=2)
    # id desc 顺序：最新两条
    assert [r.id for r in rows] == sorted(ids, reverse=True)[:2]
    assert len(rows) == 2


def test_search_sprints_service_no_match(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_sprints(s, q="不存在的关键词xyz", limit=10)
    assert rows == []


def test_search_sprints_api_endpoint(seeded):
    p_id, ids = seeded
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    resp = client.get("/api/search/sprints", params={"q": "Beta"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["id"] == ids[1]
    assert data[0]["title"] == "Sprint Beta 看板冲刺"
    assert data[0]["project_id"] == p_id
    # 序列化契约：_ser 全列（status 供前端 hint）
    assert "goal" in data[0] and "status" in data[0]


def test_search_sprints_api_route_not_conflict_project_sprints(seeded):
    """/api/search/sprints 不得被 /api/projects/{pid}/sprints 项目级路由捕获。"""
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    resp = client.get("/api/search/sprints", params={"q": "Sprint"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 3


def test_search_sprints_api_q_required_and_limit_cap(seeded):
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    # q 缺省 → 422
    assert client.get("/api/search/sprints").status_code == 422
    # limit 超过 50 → 422
    assert client.get("/api/search/sprints", params={"q": "Sprint", "limit": 51}).status_code == 422
    # limit 上限 50 合法
    assert client.get("/api/search/sprints", params={"q": "Sprint", "limit": 50}).status_code == 200


def test_search_sprints_endpoints_coexist(seeded):
    """/api/search/sprints 与 /api/search/epics、/api/search/stories 并存互不干扰。"""
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    assert client.get("/api/search/sprints", params={"q": "Sprint"}).status_code == 200
    assert client.get("/api/search/epics", params={"q": "Epic"}).status_code == 200
    assert client.get("/api/search/stories", params={"q": "Story"}).status_code == 200
