"""Epic 122 切片 3 M1（Story 232 / Task 1013）：Webhook 事件接入。

覆盖（对应任务验收）：
1. service.fire_webhooks_for_event：
   - 无 webhook / 全部 disabled → {matched:0, succeeded:0}；
   - events 空列表 → 订阅全部事件；非空 → 精确匹配才派发，不匹配跳过；
   - 单 webhook 失败（网络异常/非 2xx）隔离，不影响其它；返回统计正确；
2. API 接入点（TestClient + mock api._notify_webhooks）：
   - create_story → story.created + project_id 正确；
   - review_story approve → story.ready；reject → review.rejected；
   - submit_task_for_review → task.ready_for_review；
   - review_task approve → task.reviewed；
   - _notify_webhooks 本身 best-effort（fire_webhooks_for_event 抛异常不冒泡）；
3. MCP AST 护栏：Epic 97 零 _api( 残留；webhook 工具已注册（Epic 97 修复）。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_s3m1.py -q
"""
import ast
import itertools
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, auth, mq, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.models import Task  # noqa: E402
from agentboard.mq import (  # noqa: E402
    EVENT_COMMENT_REPLIED,
    EVENT_REVIEW_REQUESTED,
    EVENT_REVIEW_REJECTED,
    EVENT_STORY_CREATED,
    EVENT_STORY_READY,
    EVENT_TASK_READY_FOR_REVIEW,
    EVENT_TASK_REJECTED,
    EVENT_TASK_REVIEWED,
)

init_db()

_MCP_SOURCE = Path(_ROOT) / "agentboard" / "mcp_server.py"
_SEQ = itertools.count(1)

#: Epic 97 已修复的 webhook 工具（S3 M1 复用，不新增）
_WEBHOOK_TOOLS = {
    "create_webhook": ("/api/webhooks", "POST"),
    "list_webhooks": ("/api/webhooks", "GET"),
    "delete_webhook": ("/api/webhooks/", "DELETE"),
    "toggle_webhook": ("/api/webhooks/", "PATCH"),
}


def _seed():
    """1 项目 + dev（作者）+ rev（在线 reviewer Agent，供评审流使用）。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"S3M1 P{n}")
        dev = service.register_user(s, username=f"s3m1-dev{n}", password="password123")
        rev = service.register_user(s, username=f"s3m1-rev{n}", password="password123")
        for uid in (dev.id, rev.id):
            service.add_project_member(s, project_id=p.id, user_id=uid, role="member")
        service.register_agent(s, agent_id=f"s3m1-r-{n}", name="R",
                               roles='["reviewer"]', user_id=rev.id)
        service.agent_heartbeat(s, f"s3m1-r-{n}", user_id=rev.id)
        epic = service.create_epic(s, project_id=p.id, title=f"S3M1 Epic{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"S3M1 Story{n}")
        s.commit()
        return p.id, dev.id, rev.id, epic.id, st.id


@pytest.fixture(scope="function")
def seeded():
    return _seed()


def _make_webhook(s, project_id, *, name="wh", events=None, enabled=True):
    """直建 WebhookConfig（绕开 HTTP URL 校验限制，URL 指向不可达端口也无妨——测试 mock 掉 fire_webhook）。"""
    import json
    wh = service.WebhookConfig(
        project_id=project_id, name=name, url="http://127.0.0.1:1/hook",
        events=json.dumps(events if events is not None else []),
        enabled=enabled,
    )
    s.add(wh)
    s.flush()
    return wh


# ---------- 1. service.fire_webhooks_for_event ----------

def test_no_webhook_zero(seeded):
    pid, *_ = seeded
    with SessionLocal() as s:
        stats = service.fire_webhooks_for_event(
            s, project_id=pid, event=EVENT_STORY_CREATED, payload={"id": 1})
        assert stats == {"matched": 0, "succeeded": 0}


def test_disabled_webhook_skipped(seeded):
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, pid, name="off", enabled=False)
        s.commit()
        with mock.patch.object(service, "fire_webhook") as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_CREATED, payload={})
            assert stats == {"matched": 0, "succeeded": 0}
            fw.assert_not_called()


def test_empty_events_subscribes_all(seeded):
    """events=[] → 订阅全部事件：两个 webhook 都命中。"""
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, pid, name="a")
        _make_webhook(s, pid, name="b")
        s.commit()
        with mock.patch.object(service, "fire_webhook", return_value=True) as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_READY, payload={"id": 9})
            assert stats == {"matched": 2, "succeeded": 2}
            assert fw.call_count == 2
            for c in fw.call_args_list:
                assert c.args[1] == EVENT_STORY_READY  # (webhook, event, payload)
                assert c.args[2] == {"id": 9}


def test_exact_event_match(seeded):
    """非空 events → 精确包含才派发；其它事件跳过。"""
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, pid, name="only-ready", events=[EVENT_STORY_READY])
        _make_webhook(s, pid, name="only-review", events=[EVENT_REVIEW_REQUESTED])
        s.commit()
        with mock.patch.object(service, "fire_webhook", return_value=True) as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_READY, payload={})
            assert stats == {"matched": 1, "succeeded": 1}
            assert fw.call_count == 1
            assert fw.call_args.args[0].name == "only-ready"


def test_mismatch_event_skipped(seeded):
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, pid, name="only-review", events=[EVENT_REVIEW_REQUESTED])
        s.commit()
        with mock.patch.object(service, "fire_webhook") as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_TASK_READY_FOR_REVIEW, payload={})
            assert stats == {"matched": 0, "succeeded": 0}
            fw.assert_not_called()


def test_failure_isolated_and_stats(seeded):
    """单 webhook 失败（返回 False）隔离：matched 计入、succeeded 只计成功。"""
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, pid, name="ok")
        _make_webhook(s, pid, name="bad")
        s.commit()
        def _flaky(webhook, event, payload):
            return webhook.name != "bad"
        with mock.patch.object(service, "fire_webhook", side_effect=_flaky) as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_CREATED, payload={})
            assert stats == {"matched": 2, "succeeded": 1}
            assert fw.call_count == 2


def test_fire_webhook_exception_isolated(seeded):
    """fire_webhook 本身抛异常 → 该 webhook 计失败，不影响其它。"""
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, pid, name="boom")
        _make_webhook(s, pid, name="fine")
        s.commit()
        def _boom_or_ok(webhook, event, payload):
            if webhook.name == "boom":
                raise RuntimeError("network down")
            return True
        with mock.patch.object(service, "fire_webhook", side_effect=_boom_or_ok) as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_CREATED, payload={})
            assert stats == {"matched": 2, "succeeded": 1}
            assert fw.call_count == 2


def test_other_project_webhooks_ignored(seeded):
    pid, *_ = seeded
    with SessionLocal() as s:
        other = service.create_project(s, name=f"S3M1 Other{next(_SEQ)}")
        _make_webhook(s, other.id, name="foreign")
        _make_webhook(s, pid, name="mine")
        s.commit()
        with mock.patch.object(service, "fire_webhook", return_value=True) as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_CREATED, payload={})
            assert stats == {"matched": 1, "succeeded": 1}
            assert fw.call_args.args[0].name == "mine"


def test_global_webhook_matches(seeded):
    """project_id=NULL 的全局 webhook 对所有项目事件生效。"""
    pid, *_ = seeded
    with SessionLocal() as s:
        _make_webhook(s, None, name="global")  # 全局（project_id 为空）
        s.commit()
        with mock.patch.object(service, "fire_webhook", return_value=True) as fw:
            stats = service.fire_webhooks_for_event(
                s, project_id=pid, event=EVENT_STORY_READY, payload={})
            assert stats == {"matched": 1, "succeeded": 1}
            assert fw.call_args.args[0].name == "global"


# ---------- 2. API 接入点断言 ----------

def _client():
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_create_story_notifies(seeded):
    pid, _, _, eid, _ = seeded
    c = _client()
    with mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post(f"/api/epics/{eid}/stories",
                   json={"title": "S3M1 webhook story", "description": ""})
        assert r.status_code == 201, r.text
        nw.assert_called_once()
        args = nw.call_args.args
        assert args[1] == pid
        assert args[2] == EVENT_STORY_CREATED
        assert args[3]["id"] == r.json()["id"]
        assert args[3]["status"] == "backlog"


def test_api_review_story_deprecated_422(seeded):
    """Story 评审已下线（2026-08-09）：approve/reject 端点均返回 422 且不通知 webhook。"""
    pid, dev, rev, _, sid = seeded
    c = _client()
    with mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post(f"/api/stories/{sid}/review",
                   headers={"Authorization": f"Bearer {auth.make_token(rev)}"},
                   json={"verdict": "approve", "comment": "S3M1 ok"})
        assert r.status_code == 422, r.text
        assert "评审已下线" in r.json().get("detail", "")
        nw.assert_not_called()
    with mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post(f"/api/stories/{sid}/review",
                   headers={"Authorization": f"Bearer {auth.make_token(rev)}"},
                   json={"verdict": "reject", "comment": "需补充"})
        assert r.status_code == 422, r.text
        assert "评审已下线" in r.json().get("detail", "")
        nw.assert_not_called()


def test_api_submit_review_notifies(seeded):
    """submit-review → task.ready_for_review webhook。"""
    pid, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = Task(project_id=pid, story_id=sid, title="T-submit", status="todo")
        s.add(t)
        s.flush()
        service.claim_development_task(s, t.id, user_id=dev)
        tid = t.id
        s.commit()
    c = _client()
    with mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post(f"/api/tasks/{tid}/submit-review",
                   headers={"Authorization": f"Bearer {auth.make_token(dev)}"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "in_review"
        nw.assert_called_once()
        args = nw.call_args.args
        assert args[1] == pid
        assert args[2] == EVENT_TASK_READY_FOR_REVIEW
        assert args[3]["id"] == tid


def test_api_review_task_approve_notifies(seeded):
    """review approve → task.reviewed webhook。"""
    pid, dev, rev, _, sid = seeded
    with SessionLocal() as s:
        t = Task(project_id=pid, story_id=sid, title="T-approve", status="todo")
        s.add(t)
        s.flush()
        service.claim_development_task(s, t.id, user_id=dev)
        service.submit_task_for_review(s, t.id, user_id=dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev  # 固定评审人（assign 随机）
        s.flush()
        tid = t.id
        s.commit()
    c = _client()
    with mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post(f"/api/tasks/{tid}/review",
                   headers={"Authorization": f"Bearer {auth.make_token(rev)}"},
                   json={"verdict": "approve", "comment": "S3M1 LGTM"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "done"
        nw.assert_called_once()
        args = nw.call_args.args
        assert args[1] == pid
        assert args[2] == EVENT_TASK_REVIEWED
        assert args[3]["status"] == "done"


def test_api_review_task_reject_notifies(seeded):
    pid, dev, rev, _, sid = seeded
    with SessionLocal() as s:
        t = Task(project_id=pid, story_id=sid, title="T-reject", status="todo")
        s.add(t)
        s.flush()
        service.claim_development_task(s, t.id, user_id=dev)
        service.submit_task_for_review(s, t.id, user_id=dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev
        s.flush()
        tid = t.id
        s.commit()
    c = _client()
    with mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post(f"/api/tasks/{tid}/review",
                   headers={"Authorization": f"Bearer {auth.make_token(rev)}"},
                   json={"verdict": "reject", "comment": "有 bug"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "in_progress"
        nw.assert_called_once()
        args = nw.call_args.args
        assert args[1] == pid
        assert args[2] == EVENT_TASK_REJECTED
        assert args[3]["review_round"] == 1


def test_notify_webhooks_best_effort(seeded):
    """_notify_webhooks 内部异常不冒泡（主业务不受影响）。"""
    pid, _, _, eid, _ = seeded
    c = _client()
    with mock.patch.object(service, "fire_webhooks_for_event",
                    side_effect=RuntimeError("db gone")) as fw:
        r = c.post(f"/api/epics/{eid}/stories", json={"title": "best-effort"})
        assert r.status_code == 201, r.text  # 主业务成功
        fw.assert_called_once()


# ---------- 3. MCP AST 护栏（Epic 97 零 _api 残留 + webhook 工具注册） ----------

def _is_mcp_tool_decorator(d: ast.AST) -> bool:
    return (isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "mcp")


def test_epic97_ast_guard_no_api_leftovers():
    """Epic 97 护栏：mcp_server.py 不得再出现 _api( 调用。"""
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    leftovers = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_api"
    ]
    assert not leftovers, f"mcp_server.py 仍有 _api( 残留：行 {leftovers}"


def test_webhook_tools_registered_in_mcp_server():
    """S3 M1 复用 webhook 工具：create/list/delete/toggle 必须已带 @mcp.tool() 且命中 REST。"""
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    tool_names: set[str] = set()
    rest_calls: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            if any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                tool_names.add(node.name)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_http"):
            args = node.args
            if len(args) < 2 or not isinstance(args[0], ast.Constant):
                continue
            method = args[0].value
            pnode = args[1]
            if isinstance(pnode, ast.Constant) and isinstance(pnode.value, str):
                literal = pnode.value
            elif isinstance(pnode, ast.JoinedStr) and pnode.values:
                head = pnode.values[0]
                if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
                    continue
                literal = head.value
            else:
                continue
            rest_calls.append((method, literal))
    missing = set(_WEBHOOK_TOOLS) - tool_names
    assert not missing, f"webhook 工具未注册：{missing}"
    for tool, (path_frag, method) in _WEBHOOK_TOOLS.items():
        assert any(m == method and path_frag in p for m, p in rest_calls), (
            f"{tool} 缺少 {method} {path_frag} 的 _http 调用")
