"""评论写操作 e2e（双栈重构 P2a）。

针对运行中的 .NET BFF（端口 18099，真实 agentboard.db）验证评论创建/列表/删除
与 FastAPI work_items router 1:1 对齐：路由布局、状态码、校验语义。

运行模式：
- 默认：连 AGENTBOARD_BFF_URL（运行中实例，真实库含 FastAPI 共享表）；不可达 skip。
- E2E_SPINUP=1：自拉起临时空 SQLite，无 FastAPI 拥有的共享表，取不到种子 id 而 skip
  （与 conftest 设计一致，不污染 spinup 绿态）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _seed_ids(client):
    """从真实库取一个 task/story/epic id；取不到（空库/异常）返回 None。"""
    try:
        tasks = client.get("/api/tasks")
        stories = client.get("/api/stories")
        epics = client.get("/api/epics")
    except Exception:  # noqa: BLE001
        return None
    if tasks.status_code != 200 or stories.status_code != 200 or epics.status_code != 200:
        return None
    t, s, e = tasks.json(), stories.json(), epics.json()
    if not t or not s or not e:
        return None
    return t[0]["id"], s[0]["id"], e[0]["id"]


@pytest.fixture(scope="module")
def ids(bff_client):
    s = _seed_ids(bff_client)
    if s is None:
        pytest.skip("真实库无种子数据（E2E_SPINUP 空库场景），skip 评论写 e2e")
    return s


def test_post_task_comment_returns_201(ids, bff_client):
    task_id, _, _ = ids
    r = bff_client.post(
        f"/api/tasks/{task_id}/comments",
        json={"author": "e2e-alice", "content": "e2e first comment"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["task_id"] == task_id
    assert body["story_id"] is None
    assert body["epic_id"] is None
    assert body["author"] == "e2e-alice"
    assert body["content"] == "e2e first comment"
    assert body["id"] > 0
    bff_client.delete(f"/api/comments/{body['id']}")


def test_post_story_and_epic_comments_201(ids, bff_client):
    _, story_id, epic_id = ids
    rs = bff_client.post(
        f"/api/stories/{story_id}/comments",
        json={"author": "e2e-bob", "content": "on story"},
    )
    assert rs.status_code == 201
    assert rs.json()["story_id"] == story_id
    bff_client.delete(f"/api/comments/{rs.json()['id']}")

    re = bff_client.post(
        f"/api/epics/{epic_id}/comments",
        json={"author": "e2e-carol", "content": "on epic"},
    )
    assert re.status_code == 201
    assert re.json()["epic_id"] == epic_id
    bff_client.delete(f"/api/comments/{re.json()['id']}")


def test_list_task_comments_contains_new(ids, bff_client):
    task_id, _, _ = ids
    create = bff_client.post(
        f"/api/tasks/{task_id}/comments",
        json={"author": "e2e-alice", "content": "list me"},
    )
    cid = create.json()["id"]
    try:
        lst = bff_client.get(f"/api/tasks/{task_id}/comments")
        assert lst.status_code == 200
        assert "list me" in [c["content"] for c in lst.json()]
    finally:
        bff_client.delete(f"/api/comments/{cid}")


def test_delete_comment_ok_then_404(ids, bff_client):
    task_id, _, _ = ids
    create = bff_client.post(
        f"/api/tasks/{task_id}/comments",
        json={"author": "e2e-alice", "content": "to delete"},
    )
    cid = create.json()["id"]
    d1 = bff_client.delete(f"/api/comments/{cid}")
    assert d1.status_code == 200
    assert d1.json().get("ok") is True
    d2 = bff_client.delete(f"/api/comments/{cid}")
    assert d2.status_code == 404


def test_post_comment_missing_target_404(ids, bff_client):
    r = bff_client.post(
        "/api/tasks/999999999/comments",
        json={"author": "e2e", "content": "ghost"},
    )
    assert r.status_code == 404


def test_post_comment_empty_author_content_422(ids, bff_client):
    task_id, _, _ = ids
    r1 = bff_client.post(
        f"/api/tasks/{task_id}/comments", json={"author": "", "content": ""}
    )
    assert r1.status_code == 422
    r2 = bff_client.post(f"/api/tasks/{task_id}/comments", json={})
    assert r2.status_code == 422


def test_author_truncated_to_100(ids, bff_client):
    task_id, _, _ = ids
    r = bff_client.post(
        f"/api/tasks/{task_id}/comments",
        json={"author": "x" * 250, "content": "trim"},
    )
    assert r.status_code == 201
    assert len(r.json()["author"]) == 100
    bff_client.delete(f"/api/comments/{r.json()['id']}")
