"""Epic 全局搜索端点回归护栏（Task xxx，Epic 119 v6.13 命令面板接入 Epic 搜索）。

覆盖：
1. service.search_epics：按标题/描述关键词匹配，id desc + limit；
2. API 端点 /api/search/epics：200 结构、limit 上限 50、q 必填；
3. 路由冲突：/api/search/epics 不被 /api/epics/{eid} 拦截（返回 200 而非 422）；
4. 与 Story 搜索端点 /api/search/stories 并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic119_search_epics.py -q
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
    """创建 1 项目 × 3 Epic：标题/描述关键词各不相同，便于精确匹配。"""
    with SessionLocal() as s:
        p = service.create_project(s, name="SearchEpics P")
        titles = [
            ("Epic Alpha Query 引擎", "desc alpha 描述"),
            ("Epic Beta 看板视图", "desc beta 描述"),
            ("Epic Gamma 命令面板", "desc gamma 描述"),
        ]
        ids = []
        for title, desc in titles:
            epic = service.create_epic(s, project_id=p.id, title=title, description=desc)
            ids.append(epic.id)
        s.commit()
        return p.id, ids


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_epics_service_title(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_epics(s, q="Alpha", limit=10)
    assert [r.id for r in rows] == [ids[0]]
    assert rows[0].title == "Epic Alpha Query 引擎"


def test_search_epics_service_description(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_epics(s, q="看板", limit=10)
    assert [r.id for r in rows] == [ids[1]]


def test_search_epics_service_limit_and_order(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_epics(s, q="Epic", limit=2)
    # id desc 顺序：最新两条
    assert [r.id for r in rows] == sorted(ids, reverse=True)[:2]
    assert len(rows) == 2


def test_search_epics_service_no_match(seeded):
    p_id, ids = seeded
    with SessionLocal() as s:
        rows = service.search_epics(s, q="不存在的关键词xyz", limit=10)
    assert rows == []


def test_search_epics_api_endpoint(seeded):
    p_id, ids = seeded
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    resp = client.get("/api/search/epics", params={"q": "Alpha"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["id"] == ids[0]
    assert data[0]["title"] == "Epic Alpha Query 引擎"
    assert data[0]["project_id"] == p_id
    # 序列化契约：_ser 全列
    assert "description" in data[0] and "status" in data[0]


def test_search_epics_api_route_not_captured_by_eid(seeded):
    """/api/search/epics 不得被 /api/epics/{eid:int} 捕获（search 非 int → 422 即失败）。"""
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    resp = client.get("/api/search/epics", params={"q": "Epic"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 3


def test_search_epics_api_q_required_and_limit_cap(seeded):
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    # q 缺省 → 422
    assert client.get("/api/search/epics").status_code == 422
    # limit 超过 50 → 422
    assert client.get("/api/search/epics", params={"q": "Epic", "limit": 51}).status_code == 422
    # limit 上限 50 合法
    assert client.get("/api/search/epics", params={"q": "Epic", "limit": 50}).status_code == 200


def test_search_epics_story_endpoints_coexist(seeded):
    """/api/search/epics 与 /api/search/stories 并存互不干扰。"""
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    assert client.get("/api/search/epics", params={"q": "Epic"}).status_code == 200
    assert client.get("/api/search/stories", params={"q": "Story"}).status_code == 200
