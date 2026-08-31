"""Sprint 12 (Generic AgentWorker) — Task 评审多数决 fan-out。

覆盖：
1. ``assign_task_reviewer(count=N)`` 一次挑 N 个 reviewer：第 1 位写入
   ``Task.reviewer_id``（兼容旧查询），第 2..N 位插入 ``review_votes`` 的
   NULL verdict 占位行——投票时再落 approve/reject。
2. 同一 (entity, reviewer) 唯一约束防重：重复调用 count 不累积。
3. 单 review 模式行为不变（count=1 走原有路径）。
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
        for i, r in enumerate(reviewers):
            aid = f"s12-a{i}-{n}"
            worker_id = f"s12-worker-{i}-{n}"
            service.register_agent(s, agent_id=aid, name=f"R{i}",
                                   roles="[]", user_id=r.id)
            service.agent_heartbeat(s, aid, user_id=r.id)
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


def _inreview_task(s, project_id, *, assignee_id):
    from agentboard.models import Task
    t = Task(project_id=project_id, title="S12 task", type="dev",
             status="in_review", assignee_id=assignee_id)
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

def test_assign_task_reviewer_count_3_seeds_three_pending_votes(seeded, monkeypatch):
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        t_id = t.id

        # 一次 count=3：3 个 reviewer 全部塞进 review_votes（NULL verdict）
        t = service.assign_task_reviewer(s, t_id, count=3)
        s.commit()

        # 第一位沿用 Task.reviewer_id（向后兼容），其余 2 位走 review_votes
        assert t.reviewer_id in (r1, r2, r3)
        rows = _pending_assignments(s, "task", t_id)
        assert len(rows) == 3
        assigned_user_ids = {uid for uid, _ in rows}
        # 三个 reviewer 全部在场；都还没投票
        assert assigned_user_ids == {r1, r2, r3}
        assert all(verdict is None for _, verdict in rows)


def test_assign_task_reviewer_count_2_idempotent_when_already_three(seeded):
    """已分配 3 票后再调 count=2：no-op，不重复塞。"""
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=3)
        s.commit()
        # 第二次 count=2 应该不动（已 >= 2）
        service.assign_task_reviewer(s, t.id, count=2)
        s.commit()
        rows = _pending_assignments(s, "task", t.id)
        assert len(rows) == 3


def test_assign_task_reviewer_count_1_keeps_legacy_path(seeded):
    """单 review 模式：count=1 走旧路径，review_votes 也补一行占位（向后兼容）"""
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=1)
        s.commit()
        t = s.query(type(t)).get(t.id)
        assert t.reviewer_id in (r1, r2, r3)
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
    """多数决 fan-out 留下的 NULL verdict 行不参与票数计算。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=3)
        s.commit()

        # 3 票全部 pending → approve+reject=0，远低于 quorum=3
        approve, reject = service._review_vote_counts(s, "task", t.id)
        assert (approve, reject) == (0, 0)

        # r1 投 approve → approve=1，仍低于 quorum
        service.review_task(s, task_id=t.id, reviewer_user_id=r1,
                             verdict="approve", comment="ok1")
        approve, reject = service._review_vote_counts(s, "task", t.id)
        assert (approve, reject) == (1, 0)

        # r2 投 reject → approve=1, reject=1；1+1=2 < 3
        service.review_task(s, task_id=t.id, reviewer_user_id=r2,
                             verdict="reject", comment="no1")
        approve, reject = service._review_vote_counts(s, "task", t.id)
        assert (approve, reject) == (1, 1)

        # r3 投 approve → approve=2, reject=1；2+1=3 == quorum，结算
        t_final = service.review_task(s, task_id=t.id, reviewer_user_id=r3,
                                       verdict="approve", comment="ok3")
        # approve > reject → 多数通过
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
