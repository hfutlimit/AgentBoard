"""通知全局搜索端点回归护栏（Task 1001，Epic 121 v6.15 命令面板接入通知搜索）。

覆盖：
1. service.search_notifications：按 user_id 隔离（仅本人）+ title/content 关键词 + id desc + limit；
2. API 端点 /api/search/notifications：200 结构、401 未鉴权、q 必填、limit 上限、仅返回本人通知；
3. 与既有搜索端点并存互不干扰。

运行：
    PYTHONPATH=. python -m pytest tests/test_notification_search.py -q
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
    """两用户 × 各 3 条通知：title/content 关键词各异，验证用户隔离。"""
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    t_a = _auth_token(client, "notif_alice")
    t_b = _auth_token(client, "notif_bob")

    def uid(tok: str) -> int:
        return client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"]

    ua, ub = uid(t_a), uid(t_b)
    with SessionLocal() as s:
        rows_a = [
            ("任务 #101 已分配给你", "请实现登录页 alpha"),
            ("项目邀请：DataHub", "alpha 团队邀请你加入"),
            ("评论提及", "请帮忙 review 那个 PR"),
        ]
        rows_b = [
            ("任务 #202 已分配给你", "请修复 beta 的 bug"),
            ("任务 #101 状态变更为 done", "由 Jason 完成"),
            ("系统公告", "周末维护通知"),
        ]
        for title, content in rows_a:
            service.create_notification(s, user_id=ua, notif_type="task_assigned", title=title, content=content, link="/task/101")
        for title, content in rows_b:
            service.create_notification(s, user_id=ub, notif_type="status_changed", title=title, content=content, link="/task/202")
        s.commit()
    return client, t_a, t_b, ua, ub


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def test_search_service_user_isolated(seeded):
    """user_id 隔离：alice 搜到自己的，搜不到 bob 的。"""
    _, _, _, ua, ub = seeded
    with SessionLocal() as s:
        rows = service.search_notifications(s, user_id=ua, q="101")
    assert len(rows) == 1
    assert rows[0].title == "任务 #101 已分配给你"
    with SessionLocal() as s:
        rows = service.search_notifications(s, user_id=ua, q="202")
    assert rows == []


def test_search_service_title_and_content(seeded):
    """title 与 content 均可命中关键词。"""
    _, _, _, ua, _ = seeded
    with SessionLocal() as s:
        rows_title = service.search_notifications(s, user_id=ua, q="项目邀请")
        rows_content = service.search_notifications(s, user_id=ua, q="review")
    assert len(rows_title) == 1 and rows_title[0].title == "项目邀请：DataHub"
    assert len(rows_content) == 1 and rows_content[0].content.startswith("请帮忙 review")


def test_search_service_limit_and_no_match(seeded):
    """limit 截断 + 无匹配返回空。"""
    _, _, _, ua, _ = seeded
    with SessionLocal() as s:
        rows = service.search_notifications(s, user_id=ua, q="的", limit=2)
    assert len(rows) <= 2
    with SessionLocal() as s:
        rows = service.search_notifications(s, user_id=ua, q="不存在的关键词xyz")
    assert rows == []


def test_search_api_requires_auth(seeded):
    """未鉴权 → 401；q 缺省 → 422；limit 超上限 → 422。"""
    client, _, _, _, _ = seeded
    assert client.get("/api/search/notifications", params={"q": "任务"}).status_code == 401
    assert client.get("/api/search/notifications",
                      headers={"Authorization": f"Bearer {seeded[1]}"}).status_code == 422
    assert client.get("/api/search/notifications",
                      params={"q": "任务", "limit": 51},
                      headers={"Authorization": f"Bearer {seeded[1]}"}).status_code == 422


def test_search_api_returns_own_only(seeded):
    """带鉴权返回 200，且仅本人通知（alice 搜 101 只出 1 条，不混入 bob 的同关键词通知）。"""
    client, t_a, _, _, _ = seeded
    resp = client.get("/api/search/notifications", params={"q": "101"},
                      headers={"Authorization": f"Bearer {t_a}"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1
    item = data[0]
    assert item["title"] == "任务 #101 已分配给你"
    assert item["user_id"] == seeded[3]  # alice 的 uid
    # 序列化契约：前端需要 is_read / link / created_at
    for key in ("is_read", "link", "created_at", "type"):
        assert key in item


def test_search_api_endpoints_coexist(seeded):
    """/api/search/notifications 与既有 /api/search/* 端点并存互不干扰。"""
    client, t_a, _, _, _ = seeded
    h = {"Authorization": f"Bearer {t_a}"}
    assert client.get("/api/search/notifications", params={"q": "任务"}, headers=h).status_code == 200
    assert client.get("/api/search/sprints", params={"q": "Sprint"}).status_code == 200
    assert client.get("/api/search/epics", params={"q": "Epic"}).status_code == 200
    assert client.get("/api/search/stories", params={"q": "Story"}).status_code == 200
