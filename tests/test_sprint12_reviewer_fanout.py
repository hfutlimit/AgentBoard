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
    # T1.5：执行门判 **owner_user_id**，两列都要写（只写 created_by 会 fail-closed）
    _owner = owner_id if owner_id is not None else assignee_id
    t = Task(project_id=project_id, title="S12 task", type="dev",
             status="in_review", assignee_id=assignee_id,
             created_by_user_id=_owner, owner_user_id=_owner)
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


def _pending_agent_ids(s, entity_type, entity_id):
    """返回该实体当前 pending 票的 reviewer_agent_id 列表。

    T1.1 起唯一键是 (entity, reviewer_agent_id)，计票按 **agent** 而非 user：
    同 owner 的 3 个 agent 应当各持一票，否则 solo 部署永远凑不满 quorum。
    """
    from agentboard.features.projects.models import ReviewVote
    rows = s.query(ReviewVote.reviewer_agent_id).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).all()
    return sorted(int(a) for (a,) in rows if a is not None)


# ---------- 1. assign_task_reviewer count=N 一次挑 N 个 ----------

def test_assign_task_reviewer_count_3_fans_out_one_vote_per_agent(seeded, monkeypatch):
    """归属收敛 + per-agent 计票（T1.1）：owner 名下 3 个 agent，count=3 → 3 票。

    这里断言的是 **T1.1 改过之后的**语义。T1.1 之前唯一键是
    (entity, reviewer_user_id)，同 owner 的 3 个 agent 会被去重成 1 票 ——
    那样 solo 部署（一个 user 名下多个 agent）永远凑不满 quorum，多数决评审
    形同虚设。所以现在一人一票，本测试也随之改名并改断言。
    """
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
        # 3 个同 owner agent → 3 行 pending，user 都是 dev，agent 各不相同
        assert len(rows) == 3
        assert {uid for uid, _ in rows} == {dev_id}
        assert all(v is None for _, v in rows)
        agent_ids = _pending_agent_ids(s, "task", t_id)
        assert len(set(agent_ids)) == 3, "per-agent 计票：票必须落在不同 agent 上"


def test_assign_task_reviewer_count_2_idempotent_when_already_assigned(seeded):
    """已指派 3 票后再调 count=2：no-op，不重复塞，也不回删。"""
    pid, dev_id, [r1, r2, r3], _ = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, assignee_id=dev_id)
        s.commit()
        service.assign_task_reviewer(s, t.id, count=3)
        s.commit()
        before = _pending_agent_ids(s, "task", t.id)
        service.assign_task_reviewer(s, t.id, count=2)
        s.commit()
        rows = _pending_assignments(s, "task", t.id)
        assert len(rows) == 3
        # 幂等：agent 集合不变，没被重复插入也没被换掉
        assert _pending_agent_ids(s, "task", t.id) == before


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

        # owner(dev) 名下有 3 个 agent，只给 user_id 无法确定是哪一票 →
        # fail-closed（T1.1）。真实链路是 agent 持自己的 API key 来投票，
        # reviewer_agent_id 从凭证解析，不存在歧义。
        with pytest.raises(service.InvalidValue, match="reviewer_agent_id is required"):
            service.review_task(s, task_id=t.id, reviewer_user_id=dev_id,
                                verdict="approve", comment="ambiguous")

        # 指定 agent 投 approve → approve=1 == quorum=1 → 多数通过结算
        voter_agent_id = _pending_agent_ids(s, "task", t.id)[0]
        t_final = service.review_task(s, task_id=t.id, reviewer_user_id=dev_id,
                                      reviewer_agent_id=voter_agent_id,
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
