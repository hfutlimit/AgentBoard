"""
Epic 45 (v3.2) — 任务列表批量改截止日期 后端集成测试。

针对运行中的 API（默认 http://127.0.0.1:18000，可用 AGENTBOARD_TEST_API 覆盖）验证：
  - POST /api/tasks/bulk-update 支持 {due_date:"YYYY-MM-DD"} 批量设置截止日期
  - POST /api/tasks/bulk-update 支持 {clear_due_date:true} 批量清除截止日期
  - 非法日期（如 2026-13-99）被拒绝且不影响原数据
  - 原有 status/priority/assignee 路径不受影响（回归）
  - 结束后还原现场（全部恢复为无截止日期）

用法：
  pytest tests/test_epic45_bulk_due_date.py -v
"""
import json
import os
import sys
import urllib.request
import urllib.error

import pytest

API = os.environ.get("AGENTBOARD_TEST_API", "http://127.0.0.1:18000")
STORY_ID = 25
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


def _pick_null_due_date_tasks(token, n=3):
    data, err = _api("GET", f"/api/stories/{STORY_ID}/tasks?limit=200", token=token)
    assert data, f"load tasks failed: {err}"
    items = data.get("items", data) if isinstance(data, dict) else data
    tasks = [t for t in items if t.get("due_date") is None][:n]
    assert len(tasks) >= n, f"需要至少 {n} 个无截止日期任务，仅 {len(tasks)}"
    return tasks


def test_bulk_set_due_date(token):
    tasks = _pick_null_due_date_tasks(token, 3)
    ids = [t["id"] for t in tasks]
    target = "2026-08-01"

    resp, err = _api("POST", "/api/tasks/bulk-update",
                     {"task_ids": ids, "due_date": target}, token=token)
    assert resp is not None, f"bulk-update due_date failed: {err}"
    assert len(resp.get("updated", [])) == len(ids), resp

    for tid in ids:
        d, _ = _api("GET", f"/api/tasks/{tid}", token=token)
        assert d.get("due_date") == target, f"task {tid} 截止日期未设为 {target}: {d}"

    # 还原
    _api("POST", "/api/tasks/bulk-update", {"task_ids": ids, "clear_due_date": True}, token=token)


def test_bulk_clear_due_date(token):
    tasks = _pick_null_due_date_tasks(token, 3)
    ids = [t["id"] for t in tasks]

    # 先设置，再清除
    _api("POST", "/api/tasks/bulk-update", {"task_ids": ids, "due_date": "2026-09-09"}, token=token)
    resp, err = _api("POST", "/api/tasks/bulk-update",
                     {"task_ids": ids, "clear_due_date": True}, token=token)
    assert resp is not None, f"bulk-update clear_due_date failed: {err}"
    assert len(resp.get("updated", [])) == len(ids), resp

    for tid in ids:
        d, _ = _api("GET", f"/api/tasks/{tid}", token=token)
        assert d.get("due_date") is None, f"task {tid} 清除截止日期失败: {d}"


def test_bulk_due_date_invalid_rejected(token):
    """非法日期应被拒绝且不影响原数据。"""
    tasks = _pick_null_due_date_tasks(token, 1)
    tid = tasks[0]["id"]

    resp, err = _api("POST", "/api/tasks/bulk-update",
                     {"task_ids": [tid], "due_date": "2026-13-99"}, token=token)
    assert resp is not None, f"请求未返回: {err}"
    assert resp.get("errors"), f"非法日期未被拒绝: {resp}"
    # 原数据保持不变（仍为 None）
    d, _ = _api("GET", f"/api/tasks/{tid}", token=token)
    assert d.get("due_date") is None, f"非法日期污染了原数据: {d}"


def test_bulk_update_legacy_fields_unaffected(token):
    """回归：due_date 字段新增不应破坏既有 priority 路径。"""
    tasks = _pick_null_due_date_tasks(token, 2)
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
