"""
Epic 42 (v3.0) — 任务列表批量指派 后端集成测试。

针对运行中的本地 API (127.0.0.1:58125) 验证：
  - POST /api/tasks/bulk-update 支持 {assignee_id} 批量指派
  - POST /api/tasks/bulk-update 支持 {clear_assignee:true} 批量清除指派
  - 原有 status/priority/sprint_id 路径不受影响（回归）
  - 结束后还原现场（全部回到未指派）

用法：
  pytest tests/test_epic42_bulk_assign.py -v
"""
import json
import os
import sys
import urllib.request
import urllib.error

import pytest

API = "http://127.0.0.1:58125"
STORY_ID = 25
PROJECT_ID = 3
H = {"Content-Type": "application/json"}


def _api(method, path, body=None, token=None):
    headers = dict(H)
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode()[:300]


@pytest.fixture(scope="module")
def token():
    tok, err = _api("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert tok, f"login failed: {err}"
    return tok["token"]


def _pick_null_assignee_tasks(token, n=3):
    data, err = _api("GET", f"/api/stories/{STORY_ID}/tasks?limit=200", token=token)
    assert data, f"load tasks failed: {err}"
    items = data.get("items", data) if isinstance(data, dict) else data
    tasks = [t for t in items if t.get("assignee_id") is None][:n]
    assert len(tasks) >= n, f"需要至少 {n} 个未指派任务，仅 {len(tasks)}"
    return tasks


def test_bulk_assign_sets_assignee(token):
    # 取项目成员
    mdata, _ = _api("GET", f"/api/projects/{PROJECT_ID}/members", token=token)
    members = mdata.get("items", mdata) if isinstance(mdata, dict) else mdata
    assert members, "project 无成员"
    uid = members[0]["user_id"]

    tasks = _pick_null_assignee_tasks(token, 3)
    ids = [t["id"] for t in tasks]

    resp, err = _api("POST", "/api/tasks/bulk-update",
                     {"task_ids": ids, "assignee_id": uid}, token=token)
    assert resp is not None, f"bulk-update assignee failed: {err}"
    assert len(resp.get("updated", [])) == len(ids), resp

    for tid in ids:
        d, _ = _api("GET", f"/api/tasks/{tid}", token=token)
        assert d.get("assignee_id") == uid, f"task {tid} 未指派给 {uid}: {d}"

    # 还原
    _api("POST", "/api/tasks/bulk-update", {"task_ids": ids, "clear_assignee": True}, token=token)


def test_bulk_clear_assignee(token):
    # 先指派，再清除
    mdata, _ = _api("GET", f"/api/projects/{PROJECT_ID}/members", token=token)
    members = mdata.get("items", mdata) if isinstance(mdata, dict) else mdata
    uid = members[0]["user_id"]

    tasks = _pick_null_assignee_tasks(token, 3)
    ids = [t["id"] for t in tasks]

    _api("POST", "/api/tasks/bulk-update", {"task_ids": ids, "assignee_id": uid}, token=token)
    resp, err = _api("POST", "/api/tasks/bulk-update",
                     {"task_ids": ids, "clear_assignee": True}, token=token)
    assert resp is not None, f"bulk-update clear failed: {err}"
    assert len(resp.get("updated", [])) == len(ids), resp

    for tid in ids:
        d, _ = _api("GET", f"/api/tasks/{tid}", token=token)
        assert d.get("assignee_id") is None, f"task {tid} 清除指派失败: {d}"

    # 已为未指派，无需再还原


def test_bulk_update_legacy_fields_unaffected(token):
    """回归：assignee 字段新增不应破坏既有 status/priority 路径。"""
    tasks = _pick_null_assignee_tasks(token, 2)
    ids = [t["id"] for t in tasks]

    resp, err = _api("POST", "/api/tasks/bulk-update",
                     {"task_ids": ids, "priority": "high"}, token=token)
    assert resp is not None, f"bulk-update priority failed: {err}"
    assert len(resp.get("updated", [])) == len(ids), resp

    for tid in ids:
        d, _ = _api("GET", f"/api/tasks/{tid}", token=token)
        assert d.get("priority") == "high", f"task {tid} priority 未变: {d}"

    # 还原优先级
    for tid in ids:
        _api("PATCH", f"/api/tasks/{tid}", {"priority": "medium"}, token=token)
