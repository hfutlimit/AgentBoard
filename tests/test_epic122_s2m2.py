"""Epic 122 切片 2 M2（Story 231 / Task 1012）：Task 评审闭环。

覆盖（对应任务验收）：
1. service.assign_task_reviewer：in_review 才可指派；无在线 reviewer 拒绝；幂等；
   排除 assignee；CAS 恰一赢家（先手胜、后手回查现态）；
2. service.review_task：approve → done + 评论落库；reject → in_progress + round+1 + 评论；
   round 达上限 → blocked；非 reviewer / 非 in_review / 无 comment / 非法 verdict 拒绝；
3. service.list_task_review_tasks：reviewer_id=me 过滤 + status 过滤；
4. API 直调（TestClient + mock publish）：assign-reviewer / review 端点 200/422 + 事件断言；
   GET /api/tasks?reviewer_id=me；
5. WorkflowConsumer：task.ready_for_review → assign-reviewer 调用（mock HTTP）；
   轮询兜底扫描 in_review 未指派 Task；
6. MCP 工具 AST 注册：assign_task_reviewer / review_task / list_task_review_tasks。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_s2m2.py -q
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


from agentboard import api, auth, mq, service, workflow_worker  # noqa: E402
from agentboard.features.work_items import router as wi_router  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.models import Task  # noqa: E402
from agentboard.features.work_items.models import TaskDependency  # noqa: E402
from agentboard.mq import (  # noqa: E402
    EVENT_TASK_AVAILABLE, EVENT_TASK_READY_FOR_REVIEW, EVENT_TASK_REVIEWED,
    EVENT_TASK_REJECTED, EVENT_TASK_REVIEW_REQUESTED, WorkflowMessage,
)

init_db()

_MCP_SOURCE = (
    Path(_ROOT) / "src" / "backend-fastapi" / "agentboard" / "mcp_server.py"
    if (Path(_ROOT) / "src" / "backend-fastapi" / "agentboard" / "mcp_server.py").exists()
    else Path(_ROOT) / "agentboard" / "mcp_server.py"
)
_SEQ = itertools.count(1)

_S2M2_TOOLS = {
    "assign_task_reviewer": ("/api/tasks/", "POST"),
    "review_task": ("/api/tasks/", "POST"),
    "list_task_review_tasks": ("/api/tasks", "GET"),
}


def _seed():
    """1 项目 + dev(作者) + rev1/rev2(在线 reviewer Agent) + outsider。

    function-scope：每次重建独立实体 + 重新心跳置在线（避免测试间 agent
    online 状态相互污染，如「全部下线」用例）。
    """
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"S2M2 P{n}")
        dev = service.register_user(s, username=f"s2m2-dev{n}", password="password123")
        rev1 = service.register_user(s, username=f"s2m2-rev1{n}", password="password123")
        rev2 = service.register_user(s, username=f"s2m2-rev2{n}", password="password123")
        outsider = service.register_user(s, username=f"s2m2-out{n}", password="password123")
        for uid in (dev.id, rev1.id, rev2.id, outsider.id):
            service.add_project_member(s, project_id=p.id, user_id=uid, role="member")
        # 两个在线 reviewer Agent（角色 reviewer，绑定 rev1/rev2）
        service.register_agent(s, agent_id=f"s2m2-r1-{n}", name="R1",
                               roles='["reviewer"]', user_id=rev1.id)
        service.agent_heartbeat(s, f"s2m2-r1-{n}", user_id=rev1.id)
        service.register_agent(s, agent_id=f"s2m2-r2-{n}", name="R2",
                               roles='["reviewer"]', user_id=rev2.id)
        service.agent_heartbeat(s, f"s2m2-r2-{n}", user_id=rev2.id)
        epic = service.create_epic(s, project_id=p.id, title=f"S2M2 Epic{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"S2M2 Story{n}")
        s.commit()
        return p.id, dev.id, rev1.id, rev2.id, outsider.id, st.id


@pytest.fixture(scope="function")
def seeded():
    return _seed()


def _make_task(s, story_id, project_id, title="T", status="todo",
               assignee_id=None, reviewer_id=None, review_round=0, type="dev"):
    t = Task(project_id=project_id, story_id=story_id, title=title,
             status=status, assignee_id=assignee_id,
             reviewer_id=reviewer_id, review_round=review_round, type=type)
    s.add(t)
    s.flush()
    return t


def _claim_and_submit(s, t, dev):
    """走真实链路：backlog → claim(dev) → submit-review → in_review。"""
    service.claim_development_task(s, t.id, user_id=dev)
    service.submit_task_for_review(s, t.id, user_id=dev)


# ---------- 1. service.assign_task_reviewer ----------

def test_assign_reviewer_in_review_task(seeded):
    pid, dev, rev1, rev2, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-assign")
        _claim_and_submit(s, t, dev)
        t2 = service.assign_task_reviewer(s, t.id)
        assert t2.reviewer_id in (rev1, rev2)
        assert t2.reviewer_id != dev  # 排除作者
        assert t2.status == "in_review"
        s.rollback()


def test_reviewer_assignment_uses_matching_not_random_choice(seeded):
    pid, dev, rev1, rev2, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-ranked")
        _claim_and_submit(s, t, dev)
        worse = s.query(service.Agent).filter(service.Agent.user_id == rev2).one()
        with mock.patch(
            "agentboard.features.scheduling.service.random", create=True,
        ) as random_module:
            random_module.choice.return_value = worse
            assigned = service.assign_task_reviewer(s, t.id)
            random_module.choice.assert_not_called()
        assert assigned.reviewer_id == rev1
        s.rollback()


def test_claim_rejects_unfinished_blocking_dependency(seeded):
    pid, dev, _, _, _, sid = seeded
    with SessionLocal() as s:
        blocker = _make_task(s, sid, pid, title="T-blocker")
        target = _make_task(s, sid, pid, title="T-dependent")
        s.add(TaskDependency(task_id=target.id, depends_on_id=blocker.id,
                             dependency_type="blocks"))
        s.commit()

        with pytest.raises(service.InvalidValue, match="blocked by dependencies"):
            service.claim_development_task(s, target.id, user_id=dev)

        s.rollback()


def test_assign_reviewer_idempotent(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-idem")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        t2 = service.assign_task_reviewer(s, t.id)
        assert t2.reviewer_id == rev1  # 已指派不换人
        s.rollback()


def test_assign_reviewer_not_in_review_rejected(seeded):
    pid, dev, _, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-notreview", status="todo")
        with pytest.raises(service.InvalidValue) as ei:
            service.assign_task_reviewer(s, t.id)
        assert "not in_review" in str(ei.value)
        s.rollback()


def test_assign_reviewer_no_online_reviewer_rejected(seeded):
    """无在线 reviewer Agent（或在线但非项目成员）→ 明确错误。"""
    pid, dev, _, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-noonline")
        _claim_and_submit(s, t, dev)
        # 两个 reviewer 全部下线
        for a in service.list_agents(s, online=True):
            service.agent_deregister(s, a.agent_id)
        s.commit()
        with pytest.raises(service.InvalidValue) as ei:
            service.assign_task_reviewer(s, t.id)
        assert "no online reviewer" in str(ei.value)
        s.rollback()


def test_assign_reviewer_cas_single_winner(seeded):
    """CAS 并发：先手成功指派，后手回查返回同现态（不覆盖、不报错）。"""
    pid, dev, rev1, rev2, _, sid = seeded
    with SessionLocal() as s1:
        t = _make_task(s1, sid, pid, title="T-cas")
        _claim_and_submit(s1, t, dev)
        tid = t.id
        s1.commit()
        # 写者 A 成功
        t_a = service.assign_task_reviewer(s1, tid)
        s1.commit()
        winner = t_a.reviewer_id
        assert winner in (rev1, rev2)
        # 写者 B（另一 session）并发后到 → 幂等回查，winner 不变
        with SessionLocal() as s2:
            t_b = service.assign_task_reviewer(s2, tid)
            assert t_b.reviewer_id == winner
            cur = service.get_task(s2, tid)
            assert cur.reviewer_id == winner
        s1.rollback()


# ---------- 2. service.review_task ----------

def test_review_approve_sets_done_and_comment(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-ok")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        t2 = service.review_task(s, task_id=t.id, reviewer_user_id=rev1,
                                 verdict="approve", comment="LGTM")
        assert t2.status == "done"
        assert t2.status_reason == "completed"
        assert t2.review_round == 0
        # 评审意见落评论（唯一载体）
        comments = service.list_comments(s, task_id=t.id)
        assert any("LGTM" in c.content for c in comments)
        assert next(c for c in comments if c.content == "LGTM").author == "R1"
        s.rollback()


def test_review_reject_returns_in_progress_and_increments_round(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-rej")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        t2 = service.review_task(s, task_id=t.id, reviewer_user_id=rev1,
                                 verdict="reject", comment="需要修复")
        assert t2.status == "in_progress"  # 退回开发
        assert t2.review_round == 1
        assert t2.reviewer_id == rev1  # 评审人保留
        comments = service.list_comments(s, task_id=t.id)
        assert any("需要修复" in c.content for c in comments)
        s.rollback()


def test_review_reject_round_limit_blocks(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-block",
                       review_round=service.MAX_REVIEW_ROUNDS - 1)
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        t2 = service.review_task(s, task_id=t.id, reviewer_user_id=rev1,
                                 verdict="reject", comment="第5轮仍未收敛")
        assert t2.status == "blocked"  # 护栏
        assert t2.review_round == service.MAX_REVIEW_ROUNDS
        assert t2.status_reason == "pending_requirement_change"
        assert t2.previous_status == "in_review"
        s.rollback()


def test_review_only_assigned_reviewer(seeded):
    pid, dev, rev1, rev2, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-wrongrev")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        with pytest.raises(service.InvalidValue) as ei:
            service.review_task(s, task_id=t.id, reviewer_user_id=rev2,
                                verdict="approve", comment="x")
        assert "only the assigned reviewer" in str(ei.value)
        s.rollback()


def test_review_not_in_review_rejected(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-wrongstate",
                       status="done", reviewer_id=rev1)
        s.flush()
        with pytest.raises(service.InvalidValue) as ei:
            service.review_task(s, task_id=t.id, reviewer_user_id=rev1,
                                verdict="approve", comment="x")
        assert "not in_review" in str(ei.value)
        s.rollback()


def test_review_comment_required(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-nocomment")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        with pytest.raises(service.InvalidValue) as ei:
            service.review_task(s, task_id=t.id, reviewer_user_id=rev1,
                                verdict="approve", comment="  ")
        assert "comment is required" in str(ei.value)
        s.rollback()


def test_review_invalid_verdict_rejected(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-badverdict")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        with pytest.raises(service.InvalidValue):
            service.review_task(s, task_id=t.id, reviewer_user_id=rev1,
                                verdict="maybe", comment="x")
        s.rollback()


# ---------- 3. service.list_task_review_tasks ----------

def test_list_task_review_tasks_filters_by_reviewer(seeded):
    pid, dev, rev1, rev2, _, sid = seeded
    with SessionLocal() as s:
        t1 = _make_task(s, sid, pid, title="T-mine")
        _claim_and_submit(s, t1, dev)
        service.assign_task_reviewer(s, t1.id)
        t1.reviewer_id = rev1
        s.flush()
        t2 = _make_task(s, sid, pid, title="T-notmine")
        _claim_and_submit(s, t2, dev)
        service.assign_task_reviewer(s, t2.id)
        t2.reviewer_id = rev2
        s.flush()
        mine = service.list_task_review_tasks(s, rev1)
        assert [t.id for t in mine] == [t1.id]
        # status 过滤
        t3 = _make_task(s, sid, pid, title="T-done", status="done", reviewer_id=rev1)
        s.flush()
        done = service.list_task_review_tasks(s, rev1, status="done")
        assert [t.id for t in done] == [t3.id]
        s.rollback()


# ---------- 4. API 直调 ----------

def _client():
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_assign_and_review_full_flow(seeded):
    """assign-reviewer → review(approve) 全链路 + 事件广播断言。"""
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-api-flow")
        _claim_and_submit(s, t, dev)
        tid = t.id
        s.commit()
        # 固定 reviewer 为 rev1（避免随机）
        service.assign_task_reviewer(s, tid)
        t2 = service.get_task(s, tid)
        t2.reviewer_id = rev1  # 覆盖随机指派（seed 有两个在线 reviewer）
        s.commit()
    dev_h = {"Authorization": f"Bearer {auth.make_token(dev)}"}
    rev_h = {"Authorization": f"Bearer {auth.make_token(rev1)}"}
    c = _client()
    with mock.patch.object(wi_router, "publish_workflow_event") as pub:
        # assign-reviewer（幂等，再指派仍 200）
        r = c.post(f"/api/tasks/{tid}/assign-reviewer", headers=dev_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reviewer_id"] is not None
        pub.assert_called()
        event, etype, eid = pub.call_args.args[:3]
        assert event == EVENT_TASK_REVIEW_REQUESTED and etype == "task" and eid == tid
        # review approve → done + task.reviewed 广播
        pub.reset_mock()
        r2 = c.post(f"/api/tasks/{tid}/review", headers=rev_h,
                    json={"verdict": "approve", "comment": "API ok"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "done"
        pub.assert_called_once_with(EVENT_TASK_REVIEWED, "task", tid, ref_id=rev1)


def test_api_review_targets_the_exact_owner_agent(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        owner = service.register_agent(
            s, agent_id="s2m2-owner", name="Owner",
            roles='["developer"]', user_id=dev,
        )
        owner_agent_id = owner.agent_id
        service.agent_heartbeat(s, owner.agent_id, user_id=dev)
        t = _make_task(s, sid, pid, title="T-owner-route")
        service.claim_development_task(
            s, t.id, user_id=dev, agent_registry_id=owner.id,
        )
        service.submit_task_for_review(s, t.id, user_id=dev)
        service.assign_task_reviewer(s, t.id)
        t = service.get_task(s, t.id)
        t.reviewer_id = rev1
        tid = t.id
        s.commit()

    c = _client()
    context = c.get(
        f"/api/tasks/{tid}/review-context",
        headers={"Authorization": f"Bearer {auth.make_token(rev1)}"},
    )
    assert context.status_code == 200, context.text
    assert context.json()["owner_agent_id"] == owner_agent_id
    with mock.patch.object(wi_router, "publish_workflow_event") as pub:
        r = c.post(
            f"/api/tasks/{tid}/review",
            headers={"Authorization": f"Bearer {auth.make_token(rev1)}"},
            json={"verdict": "approve", "comment": "owner route"},
        )
        assert r.status_code == 200, r.text
        assert pub.call_args.kwargs["agent_id"] == owner_agent_id


def test_api_review_reject_broadcasts_task_rejected(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-api-rej")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1  # 固定评审人（assign 为随机，断言依赖 rev1）
        s.flush()
        tid = t.id
        s.commit()
    c = _client()
    with mock.patch.object(wi_router, "publish_workflow_event") as pub:
        r = c.post(f"/api/tasks/{tid}/review",
                   headers={"Authorization": f"Bearer {auth.make_token(rev1)}"},
                   json={"verdict": "reject", "comment": "退回"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "in_progress"
        pub.assert_called_once()
        event, etype, eid = pub.call_args.args
        assert event == EVENT_TASK_REJECTED and etype == "task" and eid == tid
        assert pub.call_args.kwargs.get("ref_id") == 1  # 第一轮


def test_api_review_non_reviewer_422(seeded):
    pid, dev, rev1, rev2, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-api-nonrev")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1  # 固定为 rev1，rev2 非指派 → 422
        s.flush()
        tid = t.id
        s.commit()
    c = _client()
    r = c.post(f"/api/tasks/{tid}/review",
               headers={"Authorization": f"Bearer {auth.make_token(rev2)}"},
               json={"verdict": "approve", "comment": "x"})
    assert r.status_code == 422, r.text


def test_api_list_task_review_tasks_me(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, sid, pid, title="T-api-me")
        _claim_and_submit(s, t, dev)
        service.assign_task_reviewer(s, t.id)
        t.reviewer_id = rev1
        s.flush()
        tid = t.id
        s.commit()
    c = _client()
    r = c.get("/api/tasks", params={"reviewer_id": "me"},
              headers={"Authorization": f"Bearer {auth.make_token(rev1)}"})
    assert r.status_code == 200, r.text
    ids = [x["id"] for x in r.json()]
    assert tid in ids
    # status 过滤
    r2 = c.get("/api/tasks", params={"reviewer_id": "me", "status": "in_review"},
               headers={"Authorization": f"Bearer {auth.make_token(rev1)}"})
    assert r2.status_code == 200, r2.text
    assert all(x["status"] == "in_review" for x in r2.json())


# ---------- 5. WorkflowConsumer：task.ready_for_review → 自动指派 ----------

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


def test_task_ready_for_review_triggers_assign(seeded):
    """task.ready_for_review → POST assign-reviewer（M2 闭环入口）。"""
    client = _FakeClient(_FakeResponse(
        200, {"id": 21, "reviewer_id": 9, "status": "in_review"}))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(
        WorkflowMessage(event=EVENT_TASK_READY_FOR_REVIEW, entity_type="task",
                        entity_id=21, ref_id=4)) is True
    assert ("POST", "/api/tasks/21/assign-reviewer") in client.calls


def test_task_ready_for_review_no_reviewer_ok(seeded):
    """无在线 reviewer（422）→ ack True，轮询兜底。"""
    client = _FakeClient(_FakeResponse(422, text="no online reviewer"))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(
        WorkflowMessage(event=EVENT_TASK_READY_FOR_REVIEW, entity_type="task",
                        entity_id=22, ref_id=4)) is True


def test_task_ready_for_review_http_error_acks(seeded):
    """HTTP 500 → ack True（暂时性条件，轮询兜底重试）。"""
    client = _FakeClient(_FakeResponse(500, text="boom"))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(
        WorkflowMessage(event=EVENT_TASK_READY_FOR_REVIEW, entity_type="task",
                        entity_id=23, ref_id=4)) is True


def test_confirm_broadcast_skips_tasks_blocked_by_dependencies(seeded):
    client = _FakeClient(_FakeResponse(200, {"items": [
        {"id": 41, "status": "todo", "ready": False},
        {"id": 42, "status": "todo", "ready": True},
    ]}))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    with mock.patch.object(workflow_worker.mq, "publish_workflow_event") as pub:
        assert w._broadcast_available_tasks(4) is True

    assert [call.args[2] for call in pub.call_args_list] == [42]


class _RouteClient:
    """签名正确的路由 fake：/api/stories → 空、/api/tasks → in_review 列表、POST → 成功。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str, **kw):
        self.calls.append(("GET", path))
        if path == "/api/stories":
            return _FakeResponse(200, {"items": []})
        if path == "/api/tasks":
            return _FakeResponse(200, [
                {"id": 31, "status": "in_review", "reviewer_id": None},
                {"id": 32, "status": "in_review", "reviewer_id": 7},
            ])
        return _FakeResponse(200, {})

    def request(self, method: str, path: str, **kw):
        self.calls.append((method, path))
        return _FakeResponse(200, {"id": 31, "reviewer_id": 9, "status": "in_review"})


def test_poll_once_assigns_in_review_tasks(seeded):
    """轮询兜底：in_review 未指派 Task → 自动指派。"""
    client = _RouteClient()
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.run_poll_once() == 1  # 仅 31 未指派被处理
    assert ("POST", "/api/tasks/31/assign-reviewer") in client.calls
    assert ("POST", "/api/tasks/32/assign-reviewer") not in client.calls


# ---------- 6. MCP 工具 AST 注册 ----------

def _is_mcp_tool_decorator(d: ast.AST) -> bool:
    return (isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "mcp")


def test_s2m2_tools_registered_in_mcp_server():
    """assign_task_reviewer / review_task / list_task_review_tasks 必须带 @mcp.tool() 且命中 REST 端点。"""
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
    missing = set(_S2M2_TOOLS) - tool_names
    assert not missing, f"S2 M2 工具未注册：{missing}"
    for tool, (path_frag, method) in _S2M2_TOOLS.items():
        assert any(m == method and path_frag in p for m, p in rest_calls), (
            f"{tool} 缺少 {method} {path_frag} 的 _http 调用")


def test_proposal_convert_creates_structured_dag_task_graph(seeded):
    pid, dev, _, _, _, sid = seeded
    with SessionLocal() as s:
        p = service.create_proposal(
            s, project_id=pid, title="P-DAG",
            content="## Features\n- [ ] Task A\n- [ ] Task B",
            author_id=dev,
        )
        service.set_proposal_status(s, p.id, "queued")
        service.claim_proposal(s, p.id, agent="test-agent")
        service.set_proposal_status(s, p.id, "converged")
        p = service.get_proposal(s, p.id)
        p.converged_spec = "- [ ] Feature 1\n- [ ] Feature 2"
        s.commit()

        story_obj = s.get(service.Story, sid)
        epic_id = story_obj.epic_id
        story, tasks, _ = service.convert_proposal_to_story(
            s, p.id, epic_id=epic_id, title="Story DAG",
        )
        design_task = s.query(Task).filter(
            Task.story_id == story.id, Task.type == "design",
        ).first()
        assert design_task is not None

        # 检查 TaskDependency 中 Feature 1 和 Feature 2 均被 design_task 阻塞
        for t in tasks:
            deps = s.query(TaskDependency).filter(
                TaskDependency.task_id == t.id,
                TaskDependency.depends_on_id == design_task.id,
                TaskDependency.dependency_type == "blocks",
            ).all()
            assert len(deps) == 1

        # 检查 TaskGraph 结构化接口
        graph = service.build_proposal_task_graph(s, p.id)
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "design-1" in node_ids
        assert "qa-1" in node_ids
        assert any(e["source"] == "design-1" for e in graph["edges"])
        assert any(e["target"] == "qa-1" for e in graph["edges"])
        s.rollback()


def test_review_approval_automatically_unlocks_and_broadcasts_successor_tasks(seeded):
    pid, dev, rev1, _, _, sid = seeded
    with SessionLocal() as s:
        t_design = _make_task(s, sid, pid, title="T-Design", type="design")
        t_impl = _make_task(s, sid, pid, title="T-Impl", type="dev")
        s.add(TaskDependency(task_id=t_impl.id, depends_on_id=t_design.id,
                             dependency_type="blocks"))
        s.commit()

        # 初始状态：t_impl 因依赖未完成不可 claim
        assert service.get_task_readiness(s, t_impl)["ready"] is False

        # 将 t_design 推进到 in_review
        t_design.status = "in_review"
        t_design.reviewer_id = rev1
        s.commit()
        design_tid = t_design.id
        impl_tid = t_impl.id

    c = _client()
    with mock.patch.object(wi_router, "publish_workflow_event") as pub:
        r = c.post(
            f"/api/tasks/{design_tid}/review",
            headers={"Authorization": f"Bearer {auth.make_token(rev1)}"},
            json={"verdict": "approve", "comment": "Design approved"},
        )
        assert r.status_code == 200, r.text

        # 验证广播了 EVENT_TASK_AVAILABLE 给 t_impl
        available_calls = [
            call for call in pub.call_args_list
            if call.args[0] == EVENT_TASK_AVAILABLE and call.args[2] == impl_tid
        ]
        assert len(available_calls) >= 1

    # 验证 t_impl 现在变为 ready: True，且可以正常 claim
    with SessionLocal() as s:
        assert service.get_task_readiness(s, impl_tid)["ready"] is True
        t_assigned = service.claim_development_task(s, impl_tid, user_id=dev)
        assert t_assigned.status == "in_progress"
        s.rollback()
