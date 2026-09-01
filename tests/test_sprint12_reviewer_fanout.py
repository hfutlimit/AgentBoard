"""Sprint 12 (Generic AgentWorker) — Task 评审多数决 fan-out（归属收敛版）。

归属收敛（2026-09-01）：评审人必须与 task 同 owner，一人一票。
同 owner 的 N 个 agent 请求 count=N fan-out 会收敛为 1 票：
1. ``assign_task_reviewer(count=N)``：owner 名下 agent 指派为 reviewer，
   写 ``Task.reviewer_id`` + ``reviewer_agent_id``，``review_votes`` 一行
   NULL verdict 占位。
2. 重复调用 count 不累积（幂等）。
3. 单 review 模式（count=1）行为一致。
4. workflow_worker 在 majority 模式时把 count 透传给后端 API。
5. _review_vote_counts 不会把 pending (NULL verdict) 算进 approve/reject
   票数（这是多数决 quorum 判定的契约，不能破坏）。

运行：
    PYTHONPATH=. python -m pytest tests/test_sprint12_reviewer_fanout.py -q
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
os.environ.pop("AGENTBOARD_REVIEW_MODE", None)
os.environ.pop("AGENTBOARD_REVIEW_QUORUM", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, mq, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()

_SEQ = itertools.count(1)
_PUBLISHED: list[tuple[str, str, int]] = []


def _seed(n_reviewers: int = 3):
    """1 项目 + 1 dev + N 个 reviewer Agent（全部在线）。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"S12 P{n}")
        dev = service.register_user(s, username=f"s12-dev{n}", password="password123")
        reviewers = []
        for i in range(n_reviewers):
            r = service.register_user(
                s, username=f"s12-r{i}-{n}", password="password123")
            reviewers.append(r)
        for uid in [dev.id] + [r.id for r in reviewers]:
            service.add_project_member(s, project_id=p.id, user_id=uid, role="member")
        # 归属收敛：N 个评审 agent 全部挂 owner(dev) 名下
        for i in range(n_reviewers):
            aid = f"s12-a{i}-{n}"
            worker_id = f"s12-worker-{i}-{n}"
            service.register_agent(s, agent_id=aid, name=f"R{i}",
                                   roles="[]", user_id=dev.id)
            service.agent_heartbeat(s, aid, user_id=dev.id)
            service.register_worker(s, worker_id=worker_id, hostname="test")
            instance = service.upsert_agent_instance(
                s, worker_id=worker_id, agent_id=aid,
                executor_type="fake",
            )
            service.instance_heartbeat(
                s, instance.id, caller_worker_id=worker_id, probe_ok=True,
            )
        epic = service.create_epic(s, project_id=p.id, title=f"S12 Epic{n}")
        s.commit()
        return p.id, dev.id, [r.id for r in reviewers], epic.id


@pytest.fixture(scope="function")
def seeded():
    return _seed()


def _inreview_task(s, project_id, *, assignee_id, owner_id=None):
    from agentboard.models import Task
    # 归属收敛：owner 默认 = assignee（本文件里 owner 即 dev）
    t = Task(project_id=project_id, title="S12 task", type="dev",
             status="in_review", assignee_id=assignee_id,
             created_by_user_id=owner_id if owner_id is not None else assignee_id)
    s.add(t)
    s.flush()
    return t


def _pending_assignments(s, entity_type, entity_id):
    """返回该实体当前的 (reviewer_user_id, verdict) 列表。"""
    from agentboard.features.projects.models import ReviewVote
    rows = s.query(ReviewVote.reviewer_user_id, ReviewVote.verdict).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).all()
    return [(int(uid), v) for uid, v in rows]


# ---------- 1. assign_task_reviewer count=N 一次挑 N 个 ----------

def test_assign_task_reviewer_count_3_collapses_to_one_vote(seeded, monkeypatch):
    """归属收敛：owner 名下 3 个 agent 请求 count=3 → 一人一票收敛为 1 票。"""
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        t_id = t.id

        t = service.assign_task_reviewer(s, t_id, count=3)
        s.commit()

        # reviewer = owner 本人；reviewer_agent_id 记录具体评审 agent
        assert t.reviewer_id == dev_id
        assert t.reviewer_agent_id is not None
        rows = _pending_assignments(s, "task", t_id)
        # 一人一票：3 个同 owner agent 收敛为 1 行 pending
        assert len(rows) == 1
        assert rows[0][0] == dev_id
        assert rows[0][1] is None


def test_assign_task_reviewer_count_2_idempotent_when_already_assigned(seeded):
    """已指派后再调 count=2：no-op，不重复塞（一人一票）。"""
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=3)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=2)
        s.commit()
        rows = _pending_assignments(s, "task", t.id)
        assert len(rows) == 1


def test_assign_task_reviewer_count_1_keeps_legacy_path(seeded):
    """单 review 模式：count=1 走旧路径，review_votes 也补一行占位（向后兼容）"""
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=1)
        s.commit()
        t = s.query(type(t)).get(t.id)
        assert t.reviewer_id == dev_id  # 同 owner
        assert t.reviewer_agent_id is not None
        rows = _pending_assignments(s, "task", t.id)
        # 一行 pending 占位（旧版 review_votes 模式下只有这一行）
        assert len(rows) == 1


def test_assign_task_reviewer_count_out_of_range_rejected(seeded):
    pid, dev_id, _, _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        for bad in (0, 10, -1):
            with pytest.raises(service.InvalidValue):
                service.assign_task_reviewer(s, t.id, count=bad)


# ---------- 2. _review_vote_counts 不把 pending 算进票数 ----------

def test_review_vote_counts_ignores_pending_null_verdict(seeded, monkeypatch):
    """多数决：pending NULL 不算票；跨 owner 不能投票；owner 达 quorum 结算。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "1")
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=3)
        s.commit()

        # pending 票不算数：approve+reject=0 < quorum=1
        approve, reject = service._review_vote_counts(s, "task", t.id)
        assert (approve, reject) == (0, 0)

        # 跨 owner 用户（r1）投票 → 拒绝（归属收敛）
        with pytest.raises(service.InvalidValue):
            service.review_task(s, task_id=t.id, reviewer_user_id=r1,
                                verdict="approve", comment="x")

        # owner(dev) 投 approve → approve=1 == quorum=1 → 多数通过结算
        t_final = service.review_task(s, task_id=t.id, reviewer_user_id=dev_id,
                                      verdict="approve", comment="ok")
        assert t_final.status == "done"
        # 结算后清票
        rows = _pending_assignments(s, "task", t.id)
        assert rows == []


# ---------- 3. workflow_worker 在 majority 模式透传 count ----------

def _build_consumer_with_fake_request(fake_response):
    """构造一个 WorkflowConsumer，其 _request 走 fake；其余字段齐全。"""
    from agentboard.workflow_worker import WorkflowConsumer, WorkflowConsumerConfig
    cfg = WorkflowConsumerConfig(api_url="http://127.0.0.1:1", token="t",
                                 poll_interval=10.0, batch_size=20,
                                 http_timeout=5.0, mq=mock.Mock(enabled=False))
    # 不让 __init__ 真的建 httpx.Client：显式传一个 mock
    consumer = WorkflowConsumer(cfg, client=mock.Mock())
    consumer._request = mock.Mock(return_value=fake_response)  # type: ignore[attr-defined]
    return consumer


def test_workflow_worker_assign_passes_quorum_count_in_majority_mode(monkeypatch):
    """当 AGENTBOARD_REVIEW_MODE=majority 时,workflow_worker 必须把
    AGENTBOARD_REVIEW_QUORUM 当作 count 传给 assign-reviewer API。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "5")

    fake_response = mock.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"id": 999, "reviewer_id": 11, "status": "in_review"}

    consumer = _build_consumer_with_fake_request(fake_response)
    ok = consumer._assign_task_reviewer(42)
    assert ok is True
    # 验证 _request 收到的 (method, path, params)
    args, kwargs = consumer._request.call_args  # type: ignore[attr-defined]
    assert args[0] == "POST"
    assert args[1] == "/api/tasks/42/assign-reviewer"
    assert kwargs.get("params") == {"count": 5}


def test_workflow_worker_assign_keeps_count_1_in_single_mode(monkeypatch):
    """single 模式（默认 / 兼容）保持 count=1，行为不变。"""
    monkeypatch.delenv("AGENTBOARD_REVIEW_MODE", raising=False)
    monkeypatch.delenv("AGENTBOARD_REVIEW_QUORUM", raising=False)

    fake_response = mock.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"id": 999, "reviewer_id": 11, "status": "in_review"}

    consumer = _build_consumer_with_fake_request(fake_response)
    ok = consumer._assign_task_reviewer(42)
    assert ok is True
    args, kwargs = consumer._request.call_args  # type: ignore[attr-defined]
    assert args[0] == "POST"
    assert args[1] == "/api/tasks/42/assign-reviewer"
    assert kwargs.get("params") == {"count": 1}
