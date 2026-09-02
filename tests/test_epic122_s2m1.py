"""Epic 122 切片 2 M1（Story 231 / Task 1010）：开发任务 CAS 认领 + 提交评审 + task.available 广播。

覆盖（对应任务验收）：
1. service.claim_development_task：backlog/todo → in_progress + assignee 回填；非 claimable 拒绝；
   404；CAS 并发恰一赢家（先手胜、后手明确错误）；
2. service.submit_task_for_review：assignee → in_review；非 assignee 拒绝；admin 豁免；
   非法状态拒绝；
3. API 直调（TestClient + mock publish）：POST /api/tasks/{tid}/claim 200/409/422；
   POST /api/tasks/{tid}/submit-review 200 + 事件广播断言 / 422；
4. WorkflowConsumer：story.ready → 回查 Story 任务 → 逐个广播 task.available（mock HTTP + mock publish）；
   无 backlog 任务不广播；task.ready_for_review → ack 不调 HTTP；
5. MCP 工具 AST 注册：claim_development_task / submit_task_for_review。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_s2m1.py -q
"""
import ast
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

from agentboard import api, auth, mq, service, workflow_worker  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.models import Task  # noqa: E402
from agentboard.mq import (
    EVENT_STORY_CONFIRMED,
    EVENT_TASK_AVAILABLE, EVENT_TASK_READY_FOR_REVIEW, EVENT_STORY_READY,
    WorkflowMessage,
)
# Phase 5 后端点路由到 features/work_items/router.py，事件广播在 router 层（从 mq 导入）。
# 必须在模块顶层（del sys.modules 之后）绑定 router 引用，否则函数内 import 会拿到
# 其它测试文件重新加载后的新模块实例，与 api.app 绑定的 router 不一致 → mock 失效。
from agentboard.features.work_items import router as wi_router  # noqa: E402

init_db()

_MCP_SOURCE = Path(_ROOT) / "agentboard" / "mcp_server.py"

_S2M1_TOOLS = {
    "claim_development_task": ("/api/tasks/", "POST"),
    "submit_task_for_review": ("/api/tasks/", "POST"),
}


def _seed():
    """1 项目 + author/dev 用户 + outsider。"""
    with SessionLocal() as s:
        p = service.create_project(s, name="S2M1 P")
        dev = service.register_user(s, username="s2m1-dev", password="password123")
        other = service.register_user(s, username="s2m1-other", password="password123")
        outsider = service.register_user(s, username="s2m1-outsider", password="password123")
        service.add_project_member(s, project_id=p.id, user_id=dev.id, role="member")
        service.add_project_member(s, project_id=p.id, user_id=other.id, role="member")
        epic = service.create_epic(s, project_id=p.id, title="S2M1 Epic")
        st = service.create_story(s, epic_id=epic.id, title="S2M1 Story")
        s.commit()
        return p.id, dev.id, other.id, outsider.id, st.id


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def _make_task(s, story_id, project_id, title="T", status="todo", assignee_id=None,
               created_by=None):
    # 直接构造 Task 对象以覆盖任意初始状态（create_task 不暴露 status）
    # 归属收敛：claim 门槛要求 owner；默认由调用处显式传 created_by。
    # T1.5：执行门判 **owner_user_id**，这里必须一并写 —— 只写 created_by 的
    # 话 owner 是 NULL，claim / 派发 / 评审全链路 fail-closed。
    t = Task(project_id=project_id, story_id=story_id, title=title,
             status=status, assignee_id=assignee_id,
             created_by_user_id=created_by, owner_user_id=created_by)
    s.add(t)
    s.flush()
    return t


def _owned_task(s, story_id, project_id, dev, **kw):
    """归属收敛便捷构造：owner=dev 的 task（claim 门槛要求同 owner）。"""
    return _make_task(s, story_id, project_id, created_by=dev, **kw)


# ---------- 1. service.claim_development_task ----------

def test_claim_backlog_task(seeded):
    _, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev)
        t2 = service.claim_development_task(s, t.id, user_id=dev)
        assert t2.status == "in_progress"
        assert t2.assignee_id == dev
        s.rollback()  # 清理，避免污染后续用例


def test_claim_todo_task(seeded):
    _, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev, title="T-todo", status="todo")
        t2 = service.claim_development_task(s, t.id, user_id=dev)
        assert t2.status == "in_progress"
        assert t2.assignee_id == dev
        s.rollback()


# Story 265：5 状态集（verifying 已下线，归并到 in_progress；这里只测不可认领的 4 个非 todo 状态）
@pytest.mark.parametrize("status", ["in_progress", "in_review", "done", "blocked"])
def test_claim_not_claimable_rejected(seeded, status):
    _, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev, title=f"T-{status}", status=status)
        with pytest.raises(service.InvalidValue) as ei:
            service.claim_development_task(s, t.id, user_id=dev)
        assert "already claimed or not claimable" in str(ei.value)
        s.rollback()


def test_claim_missing_task_not_found(seeded):
    with SessionLocal() as s:
        with pytest.raises(service.NotFound):
            service.claim_development_task(s, 999999, user_id=seeded[1])


def test_claim_cas_single_winner(seeded):
    """CAS 并发：先手成功，后手明确错误（恰一赢家）。"""
    _, dev, _, _, sid = seeded
    with SessionLocal() as s1:
        t = _owned_task(s1, sid, seeded[0], dev, title="T-cas")
        tid = t.id
        s1.commit()
        # 写者 A 认领成功
        service.claim_development_task(s1, tid, user_id=dev)
        s1.commit()
        # 写者 B（另一 session 模拟并发后到者）认领失败：前置状态检查或 rowcount 冲突
        # 都是 CAS 恰一赢家的正确结果
        with SessionLocal() as s2:
            with pytest.raises(service.InvalidValue) as ei:
                service.claim_development_task(s2, tid, user_id=seeded[2])
            msg = str(ei.value)
            assert ("claim conflict" in msg
                    or "already claimed or not claimable" in msg
                    or "only the task owner" in msg), msg  # 归属收敛：跨 owner 也被拒
            # 回查状态未被破坏
            cur = service.get_task(s2, tid)
            assert cur.status == "in_progress"
            assert cur.assignee_id == dev
        s1.rollback()


# ---------- 2. service.submit_task_for_review ----------

def test_submit_review_by_assignee(seeded):
    _, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev, title="T-submit")
        service.claim_development_task(s, t.id, user_id=dev)
        t2 = service.submit_task_for_review(s, t.id, user_id=dev)
        assert t2.status == "in_review"
        assert t2.assignee_id == dev
        s.rollback()


def test_submit_review_non_assignee_rejected(seeded):
    _, dev, other, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev, title="T-submit-other")
        service.claim_development_task(s, t.id, user_id=dev)
        with pytest.raises(service.InvalidValue) as ei:
            service.submit_task_for_review(s, t.id, user_id=other)
        assert "only the assignee" in str(ei.value)
        s.rollback()


def test_submit_review_admin_bypass(seeded):
    _, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev, title="T-submit-admin")
        service.claim_development_task(s, t.id, user_id=dev)
        # admin 即使非 assignee 也允许提交
        t2 = service.submit_task_for_review(s, t.id, user_id=seeded[3], is_admin=True)
        assert t2.status == "in_review"
        s.rollback()


def test_submit_review_wrong_state_rejected(seeded):
    _, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, seeded[0], dev, title="T-submit-backlog", status="todo")
        with pytest.raises(service.InvalidValue) as ei:
            service.submit_task_for_review(s, t.id, user_id=dev)
        assert "not in_progress" in str(ei.value)
        s.rollback()


# ---------- 3. API 直调 ----------

def _client():
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_claim_and_submit_review_full_flow(seeded):
    """claim → submit-review 全链路 + 事件广播断言。"""
    pid, dev, _, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, pid, dev, title="T-api-flow")
        tid = t.id
        s.commit()
    headers = {"Authorization": f"Bearer {auth.make_token(dev)}"}
    c = _client()
    with mock.patch.object(wi_router, "publish_workflow_event") as pub:
        # claim
        r = c.post(f"/api/tasks/{tid}/claim", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "in_progress"
        assert body["assignee_id"] == dev
        pub.assert_not_called()  # claim 不发布事件
        # 重复 claim → 409
        r2 = c.post(f"/api/tasks/{tid}/claim", headers=headers)
        assert r2.status_code == 409, r2.text
        # submit-review → in_review + task.ready_for_review 广播
        r3 = c.post(f"/api/tasks/{tid}/submit-review", headers=headers)
        assert r3.status_code == 200, r3.text
        assert r3.json()["status"] == "in_review"
        pub.assert_called_once_with(
            EVENT_TASK_READY_FOR_REVIEW, "task", tid, ref_id=dev)


def test_api_submit_review_non_assignee_422(seeded):
    pid, dev, other, _, sid = seeded
    with SessionLocal() as s:
        t = _owned_task(s, sid, pid, dev, title="T-api-nonassign")
        service.claim_development_task(s, t.id, user_id=dev)
        s.commit()
        tid = t.id
    c = _client()
    r = c.post(f"/api/tasks/{tid}/submit-review",
               headers={"Authorization": f"Bearer {auth.make_token(other)}"})
    assert r.status_code == 422, r.text


def test_api_claim_requires_login(seeded):
    pid, _, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-api-nologin")  # 无登录先被拒，无需 owner
        tid = t.id
        s.commit()
    c = _client()
    r = c.post(f"/api/tasks/{tid}/claim")
    assert r.status_code == 422, r.text  # uid=None → claim requires login


# ---------- 4. WorkflowConsumer：story.ready → task.available 广播 ----------

class _FakeResponse:
    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text or str(json_body)

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self)


class _FakeClient:
    def __init__(self, response: _FakeResponse | None = None):
        self.calls: list[tuple[str, str]] = []
        self.response = response

    def request(self, method: str, path: str, **kw):
        self.calls.append((method, path))
        return self.response if self.response is not None else _FakeResponse(200, {})

    def get(self, path: str, **kw):
        self.calls.append(("GET", path))
        return self.response if self.response is not None else _FakeResponse(200, {})


def _cfg():
    return workflow_worker.WorkflowConsumerConfig(
        api_url="http://test", token="t", mq=mq.MQConfig())


def test_story_ready_broadcasts_available_tasks(seeded):
    """_broadcast_available_tasks（2026-08-09 起由 confirmed 编排辅助调用）：
    拉取 Story 任务 → 每个 backlog/todo 任务广播一次 task.available。"""
    client = _FakeClient(_FakeResponse(200, {"items": [
        {"id": 11, "status": "todo"},
        {"id": 12, "status": "todo"},
        {"id": 13, "status": "in_progress"},
        {"id": 14, "status": "done"},
    ]}))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    with mock.patch.object(mq, "publish_workflow_event") as pub:
        assert w._broadcast_available_tasks(5) is True
    assert ("GET", "/api/stories/5/tasks") in client.calls
    # 仅 backlog/todo 被广播，且 ref_id=story_id
    assert pub.call_count == 2
    pub.assert_any_call(EVENT_TASK_AVAILABLE, "task", 11, ref_id=5)
    pub.assert_any_call(EVENT_TASK_AVAILABLE, "task", 12, ref_id=5)


def test_story_ready_no_claimable_tasks_no_broadcast(seeded):
    client = _FakeClient(_FakeResponse(200, {"items": [
        {"id": 13, "status": "in_progress"},
    ]}))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    with mock.patch.object(mq, "publish_workflow_event") as pub:
        assert w._broadcast_available_tasks(6) is True
    pub.assert_not_called()


def test_story_confirmed_acks_and_http_error_tolerated(seeded):
    """story.confirmed 由 Proposal Worker 轮询兜底执行：本 Worker 恒 ack；
    网络异常也 ack（不触发 HTTP，无重投语义）。"""
    client = _FakeClient(_FakeResponse(500, text="boom"))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(
        WorkflowMessage(event=EVENT_STORY_CONFIRMED, entity_type="story",
                        entity_id=7, ref_id=3)) is True
    assert not client.calls


def test_task_ready_for_review_triggers_assign(seeded):
    """task.ready_for_review → 自动指派 Task reviewer（切片 2 M2 闭环入口）。"""
    client = _FakeClient(_FakeResponse(
        200, {"id": 21, "reviewer_id": 9, "status": "in_review"}))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(
        WorkflowMessage(event=EVENT_TASK_READY_FOR_REVIEW, entity_type="task",
                        entity_id=21, ref_id=4)) is True
    assert ("POST", "/api/tasks/21/assign-reviewer") in client.calls


# ---------- 5. MCP 工具 AST 注册 ----------

def _is_mcp_tool_decorator(d: ast.AST) -> bool:
    return (isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "mcp")


def test_s2m1_tools_registered_in_mcp_server():
    """claim_development_task / submit_task_for_review 必须带 @mcp.tool() 且命中 REST 端点。"""
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
    missing = set(_S2M1_TOOLS) - tool_names
    assert not missing, f"S2 M1 工具未注册：{missing}"
    for tool, (path_frag, method) in _S2M1_TOOLS.items():
        assert any(m == method and path_frag in p for m, p in rest_calls), (
            f"{tool} 缺少 {method} {path_frag} 的 _http 调用")
