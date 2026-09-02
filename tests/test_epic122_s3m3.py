"""Epic 122 切片 3 M3（Story 232 / Task 1015）：多数决评审。

覆盖（对应任务验收）：
1. 配置：get_review_mode / get_review_quorum（默认值 + env 覆盖 + 非法回退）；
2. majority Story：3 票 2 approve → 结算 ready + 清票 + 评论；
   未达 quorum 状态保持（pending_review）；review_round 不变；
3. majority Task：3 票 2 reject → 结算 in_progress + round+1（或 blocked 上限）；
4. 一人一票：同 reviewer 改票（upsert 覆盖，不重复计数）；
5. 平局：quorum=2 时 1:1 → 保守驳回（round+1，回 pending_review/in_progress）；
6. 超时兜底（scan_review_timeouts）：
   - majority + 票数不足超时 → 按现有票结算（approve 多数通过 / 平局驳回）；
   - 零票超时 → 走既有重派（stories_reassigned）；
7. single 兼容：mode=single 时 review_story/review_task 走既有逻辑
   （reviewer_id 匹配校验仍生效，非指派 reviewer 拒绝）；
8. 权限：非 reviewer 候选（离线 / 无 reviewer 角色 / Task 的 assignee）投票被拒；
9. API 事件：投票未结算 → publish vote_cast；结算 approve → ready/reviewed；
   结算 reject → rejected（mock publish 断言事件名与 ref_id）；
10. Epic 97 AST 护栏：mcp_server.py 零 _api( 残留。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_s3m3.py -q
"""
import ast
import itertools
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)
# 测试内用 monkeypatch 切换模式；此处确保默认 single
os.environ.pop("AGENTBOARD_REVIEW_MODE", None)
os.environ.pop("AGENTBOARD_REVIEW_QUORUM", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, mq, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()

_MCP_SOURCE = Path(_ROOT) / "src" / "backend-fastapi" / "agentboard" / "mcp_server.py"
_SEQ = itertools.count(1)
_ASEQ = itertools.count(1)   # 追加 agent 的唯一后缀（跨 project seed 也唯一）


def _seed():
    """1 项目 + dev + 3 个 reviewer Agent（r1/r2/r3，均在线）。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"S3M3 P{n}")
        dev = service.register_user(s, username=f"s3m3-dev{n}", password="password123")
        r1 = service.register_user(s, username=f"s3m3-r1-{n}", password="password123")
        r2 = service.register_user(s, username=f"s3m3-r2-{n}", password="password123")
        r3 = service.register_user(s, username=f"s3m3-r3-{n}", password="password123")
        for uid in (dev.id, r1.id, r2.id, r3.id):
            service.add_project_member(s, project_id=p.id, user_id=uid, role="member")
        for i, uid in ((1, r1.id), (2, r2.id), (3, r3.id)):
            aid = f"s3m3-a{i}-{n}"
            service.register_agent(s, agent_id=aid, name=f"A{i}",
                                   roles='["reviewer"]', user_id=uid)
            service.agent_heartbeat(s, aid, user_id=uid)
        # 归属收敛：owner(dev) 名下也要有在线 agent（投票/重派候选），
        # 且需 runnable instance（_online_reviewer_candidates 要求）。
        service.register_agent(s, agent_id=f"s3m3-a0-{n}", name="A0",
                               roles='["reviewer"]', user_id=dev.id)
        service.agent_heartbeat(s, f"s3m3-a0-{n}", user_id=dev.id)
        service.register_worker(s, worker_id=f"s3m3-worker-0-{n}", hostname="test")
        inst = service.upsert_agent_instance(
            s, worker_id=f"s3m3-worker-0-{n}", agent_id=f"s3m3-a0-{n}",
            executor_type="fake",
        )
        service.instance_heartbeat(
            s, inst.id, caller_worker_id=f"s3m3-worker-0-{n}", probe_ok=True)
        epic = service.create_epic(s, project_id=p.id, title=f"S3M3 Epic{n}")
        s.commit()
        return p.id, dev.id, r1.id, r2.id, r3.id, epic.id


@pytest.fixture(scope="function")
def seeded():
    return _seed()





def _inreview_task(s, project_id, *, reviewer_id, assignee_id, round_=0,
                   owner_id=None):
    from agentboard.models import Task
    # 归属收敛：owner 默认 = assignee（本文件里 dev 既是 assignee 也是 owner）
    t = Task(project_id=project_id, title="S3M3 task", type="dev",
             status="in_review", reviewer_id=reviewer_id,
             assignee_id=assignee_id, review_round=round_,
             created_by_user_id=owner_id if owner_id is not None else assignee_id)
    s.add(t)
    s.flush()
    return t


def _seed_vote(s, entity_id, reviewer_user_id, verdict):
    """数据级预插一张票（模拟历史投票；归属收敛后跨 owner 不能经 API 投票，
    但多数决计数/结算逻辑仍按票行工作）。

    T1.1：reviewer_agent_id 是 NOT NULL 且是唯一性键的一部分，预插票必须
    带上 agent 身份（取该 user 名下第一个 agent，没有就现注册一个）。
    """
    from agentboard.features.projects.models import ReviewVote
    agent = (s.query(service.Agent)
             .filter(service.Agent.user_id == reviewer_user_id)
             .first())
    if agent is None:
        agent = service.register_agent(
            s, agent_id=f"s3m3-seed-{reviewer_user_id}-{entity_id}",
            name="seed", roles='["reviewer"]', user_id=reviewer_user_id)
        s.flush()
    s.add(ReviewVote(entity_type="task", entity_id=entity_id,
                     reviewer_user_id=reviewer_user_id,
                     reviewer_agent_id=agent.id,
                     verdict=verdict, round=0))


def _votes(s, entity_type, entity_id):
    """返回该实体当前票数（approve, reject）与总票数。"""
    approve, reject = service._review_vote_counts(s, entity_type, entity_id)
    return approve, reject, approve + reject


def _add_owner_agent(s, user_id, label):
    """给 owner 再注册一个**可投票**的 agent（含 worker + runnable instance）。

    T1.1 后计票单位是 agent：同一 owner 名下挂 N 个 agent 才能投出 N 票。
    ``_online_reviewer_candidates`` 要求在线 + enabled + 项目成员 +
    有 runnable instance，四样缺一不可，所以这里连 worker/instance 一起建。
    """
    from agentboard.features.identity.service import create_api_key
    n = next(_ASEQ)
    aid = f"s3m3-{label}-{n}"
    service.register_agent(s, agent_id=aid, name=label,
                           roles='["reviewer"]', user_id=user_id)
    service.agent_heartbeat(s, aid, user_id=user_id)
    wid = f"s3m3-w-{label}-{n}"
    service.register_worker(s, worker_id=wid, hostname="test")
    inst = service.upsert_agent_instance(s, worker_id=wid, agent_id=aid,
                                         executor_type="fake")
    service.instance_heartbeat(s, inst.id, caller_worker_id=wid, probe_ok=True)
    agent = s.query(service.Agent).filter(service.Agent.agent_id == aid).first()
    s.commit()
    # 绑定 agent 的 API key —— Router 靠它拿到权威 reviewer_agent_id
    _item, plaintext = create_api_key(
        s, user_id=user_id, name=f"key-{label}",
        permissions=["api:read", "api:write"], agent_ref=aid)
    return agent, plaintext


# ---------- 1. 配置读取 ----------

def test_review_mode_default_single():
    assert service.get_review_mode() == service.REVIEW_MODE_SINGLE


def test_review_mode_env(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    assert service.get_review_mode() == service.REVIEW_MODE_MAJORITY
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "weird")
    assert service.get_review_mode() == service.REVIEW_MODE_SINGLE  # 非法回退


def test_review_quorum_env(monkeypatch):
    # T1.2（2026-09-02）：默认 3 → 1。归属收敛后能投票的实体基数很小，
    # quorum=3 会让 majority 模式永远凑不满票、任务卡到超时才被扫走。
    assert service.get_review_quorum() == 1  # 默认
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "5")
    assert service.get_review_quorum() == 5
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "1")   # 归属收敛：1 合法（单 owner 可结算）
    assert service.get_review_quorum() == 1
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "99")  # 高于上限
    assert service.get_review_quorum() == 1
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "abc")
    assert service.get_review_quorum() == 1


# ---------- 2. majority Story：达 quorum 多数通过 ----------

def test_story_majority_approved(seeded, monkeypatch):
    """归属收敛版多数决：预插 1 approve + owner(dev) 投 approve → 达 quorum=2
    多数通过 → done；票清空。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "2")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        _seed_vote(s, t_id, r1, "approve")   # 历史票（数据级）
        s.commit()

        # owner 投票前：1 票 < quorum=2
        assert _votes(s, "task", t_id) == (1, 0, 1)
        # owner(dev) 投 approve → 2 approve > 0 reject → done，票清空
        s2 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                  verdict="approve", comment="LGTM owner")
        assert s2.status == "done"
        assert _votes(s, "task", t_id) == (0, 0, 0)
        assert len(service.list_comments(s, task_id=t_id)) >= 1


def test_story_majority_rejected(seeded, monkeypatch):
    """归属收敛版：预插 1 reject + owner 投 reject → 多数驳回：round+1 回
    in_progress；票清空。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "2")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        _seed_vote(s, t_id, r1, "reject")
        s.commit()
        st3 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                   verdict="reject", comment="需求不明确")
        assert st3.status == "in_progress"
        assert st3.review_round == 1
        assert _votes(s, "task", t_id) == (0, 0, 0)


def test_story_majority_blocked_at_round_limit(seeded, monkeypatch):
    """轮次达 MAX_REVIEW_ROUNDS → blocked 护栏。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "2")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev,
                           round_=service.MAX_REVIEW_ROUNDS - 1)
        t_id = t.id
        _seed_vote(s, t_id, r1, "reject")
        s.commit()
        st3 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                   verdict="reject", comment="owner no")
        assert st3.status == "blocked"
        assert st3.review_round == service.MAX_REVIEW_ROUNDS


# ---------- 3. majority Task ----------

def test_task_majority_rejected(seeded, monkeypatch):
    """归属收敛版：预插 approve + owner 投 reject → 1:1 平局保守驳回（round+1）。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "2")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = t.id
        _seed_vote(s, t_id, r1, "approve")
        s.commit()
        assert _votes(s, "task", t_id) == (1, 0, 1)
        t3 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                 verdict="reject", comment="bug")
        assert t3.status == "in_progress"
        assert t3.review_round == 1
        assert _votes(s, "task", t_id) == (0, 0, 0)


def test_task_majority_approved(seeded, monkeypatch):
    """归属收敛版：预插 approve + owner approve → 2:0 多数通过 done。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "2")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = t.id
        _seed_vote(s, t_id, r1, "approve")
        s.commit()
        t3 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                 verdict="approve", comment="ok owner")
        assert t3.status == "done"
        assert _votes(s, "task", t_id) == (0, 0, 0)


# ---------- 4. 一人一票（upsert 改票） ----------

def test_upsert_change_vote(seeded, monkeypatch):
    """owner 改票：覆盖不重复计数；跨 owner 投票被拒。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        s.commit()
        service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                             verdict="approve", comment="v1 approve")
        assert _votes(s, "task", t_id) == (1, 0, 1)
        # owner 改票 reject → 票数仍 1（覆盖），verdict 变化
        service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                             verdict="reject", comment="v1 changed")
        assert _votes(s, "task", t_id) == (0, 1, 1)
        # 跨 owner 用户（r2）投票被拒（归属收敛）
        with pytest.raises(service.InvalidValue):
            service.review_task(s, task_id=t_id, reviewer_user_id=r2,
                                verdict="reject", comment="r2")
        st_now = s.get(service.Task, t_id)
        assert st_now.status == "in_review"


# ---------- 5. 平局（quorum=2，1:1）→ 保守驳回 ----------

def test_tie_vote_conservative_reject(seeded, monkeypatch):
    """预插 approve + owner reject → 1:1 平局 → 保守驳回。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "2")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        _seed_vote(s, t_id, r1, "approve")
        s.commit()
        st2 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                   verdict="reject", comment="no")
        assert st2.status == "in_progress"
        assert st2.review_round == 1
        assert _votes(s, "task", t_id) == (0, 0, 0)


# ---------- 6. 超时兜底结算 ----------

def test_timeout_settle_approved(seeded, monkeypatch):
    """majority + 票数不足但超时：approve 多数 → 结算 ready。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "5")  # 5 票永远凑不齐
    pid, dev, r1, r2, r3, epic_id = seeded
    now = service.utc_now()
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        st.created_at = now - timedelta(hours=2)
        t_id = st.id
        _seed_vote(s, t_id, r1, "approve")
        s.commit()
        service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                             verdict="approve", comment="ok owner")
        assert _votes(s, "task", t_id) == (2, 0, 2)
        # 投票会写评论 → 把 Task 的 updated_at 改老，模拟超时
        s.get(service.Task, t_id).updated_at = now - timedelta(hours=2)
        s.commit()
        # 超时扫描：票数 > 0 且 approve 多数 → 兜底结算 done
        res = service.scan_review_timeouts(s, project_id=pid,
                                           timeout_minutes=30, now=now)
        assert res["tasks_settled"] == 1
        assert s.get(service.Task, t_id).status == "done"


def test_timeout_settle_tie_reject(seeded, monkeypatch):
    """majority + 超时平局 → 保守驳回（防死锁）。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "5")
    pid, dev, r1, r2, r3, epic_id = seeded
    now = service.utc_now()
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        st.created_at = now - timedelta(hours=2)
        t_id = st.id
        _seed_vote(s, t_id, r1, "approve")
        s.commit()
        service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                             verdict="reject", comment="no")
        s.get(service.Task, t_id).updated_at = now - timedelta(hours=2)
        s.commit()
        res = service.scan_review_timeouts(s, project_id=pid,
                                           timeout_minutes=30, now=now)
        assert res["tasks_settled"] == 1
        st_now = s.get(service.Task, t_id)
        assert st_now.status == "in_progress"
        assert st_now.review_round == 1


def test_timeout_zero_votes_goes_reassign(seeded, monkeypatch):
    """majority + 零票超时 → 走既有重派逻辑（stories_reassigned）。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    pid, dev, r1, r2, r3, epic_id = seeded
    now = service.utc_now()
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        st.updated_at = now - timedelta(hours=2)
        t_id = st.id
        s.commit()
        res = service.scan_review_timeouts(s, project_id=pid,
                                           timeout_minutes=30, now=now)
        assert res["tasks_settled"] == 0
        assert res["tasks_reassigned"] == 1  # 重派（owner 名下 agent 在线）
        fresh = s.get(service.Task, t_id)
        # 归属收敛：重派候选 = owner(dev) 名下 agent → reviewer = dev 本人
        assert fresh.reviewer_id == dev
        assert fresh.reviewer_agent_id is not None


# ---------- 7. single 模式兼容（既有行为不变） ----------

def test_single_mode_still_enforces_reviewer(seeded):
    """默认 single：非指派 reviewer 投票被拒（既有 S1/S2 契约）。"""
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        s.commit()
        with pytest.raises(service.InvalidValue):
            service.review_task(s, task_id=t_id, reviewer_user_id=r2,
                                 verdict="approve", comment="hijack")
        # 指派 reviewer approve → ready（single 直判）
        st2 = service.review_task(s, task_id=t_id, reviewer_user_id=r1,
                                   verdict="approve", comment="ok")
        assert st2.status == "done"
        assert _votes(s, "task", t_id) == (0, 0, 0)  # 无投票表写入


def test_single_mode_task_reject_round(seeded):
    """默认 single：Task reject → round+1 回 in_progress（既有语义）。"""
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = t.id
        s.commit()
        t2 = service.review_task(s, task_id=t_id, reviewer_user_id=r1,
                                 verdict="reject", comment="fix it")
        assert t2.status == "in_progress"
        assert t2.review_round == 1


# ---------- 8. 权限：投票人须是项目在线 reviewer 候选 ----------

def test_majority_rejects_non_candidate(seeded, monkeypatch):
    """归属收敛：跨 owner 用户（无 owner 资格）投票被拒。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        s.commit()
        # r1 是别的用户（非 task owner）→ 拒绝
        with pytest.raises(service.InvalidValue):
            service.review_task(s, task_id=t_id, reviewer_user_id=r1,
                                 verdict="approve", comment="hijack")


def test_majority_owner_assignee_can_vote(seeded, monkeypatch):
    """归属收敛：owner（== assignee，user 维度）是唯一合法投票人。

    旧的「assignee user 不能自投」跨用户隔离已退休；agent 维度的
    「实现方 agent 不评审」由指派侧 exclusion 保证。
    """
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "1")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = t.id
        s.commit()
        t2 = service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                 verdict="approve", comment="owner approve")
        assert t2.status == "done"


def test_majority_rejects_offline_agent(seeded, monkeypatch):
    """归属收敛：owner 名下 agent 全部离线 → owner 不能投票（候选集=在线）。"""
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        # owner(dev) 名下 agent 全部下线
        s.query(service.Agent).filter(service.Agent.user_id == dev).update(
            {"online": False})
        s.commit()
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        s.commit()
        with pytest.raises(service.InvalidValue):
            service.review_task(s, task_id=t_id, reviewer_user_id=dev,
                                 verdict="approve", comment="offline vote")


# ---------- 9. API 事件：vote_cast / 结算事件 ----------

def _call_api_review_task(tid, reviewer, verdict, comment, token):
    from fastapi.testclient import TestClient
    with TestClient(api.app) as c:
        r = c.post(f"/api/tasks/{tid}/review",
                   json={"verdict": verdict, "comment": comment},
                   headers={"Authorization": f"Bearer {token}"})
        return r


# 事件断言的 mock 目标必须是 **router 模块**里的名字：router 在 import 期就把
# ``publish_workflow_event`` 从 mq 绑进自己的命名空间，patch
# ``api.publish_workflow_event`` 拦不到（原测试正是因此收集不到事件）。
from agentboard import api_helpers as _api_helpers  # noqa: E402
from agentboard.features.work_items import router as _wi_router  # noqa: E402


def _patch_events(mq_events, wh_events):
    return (mock.patch.object(_wi_router, "publish_workflow_event",
                              side_effect=lambda *a, **k: mq_events.append((a, k))),
            mock.patch.object(_api_helpers, "_notify_webhooks",
                              side_effect=lambda *a, **k: wh_events.append((a, k))))


def test_api_vote_cast_then_ready_event(seeded, monkeypatch):
    """API 事件判定：前 2 票 → vote_cast；第 3 票结算 → task.reviewed。

    MQ + Webhook 双通道断言：vote_cast 与 task.reviewed 均须派发（CI 护栏）。

    T1.1 改写：计票单位是 agent。归属收敛后只有 task owner 名下的 agent 能
    投票，所以三票来自 dev 名下三个不同 agent（各持一个绑定该 agent 的 API
    key）——这正是 per-agent 计票要打通的场景：按 user 计票时这里只能投出
    1 票，永远凑不满 quorum=3。
    """
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        # dev 名下已有 a0，再补两个可投票 agent
        _a1, k1 = _add_owner_agent(s, dev, "dx")
        _a2, k2 = _add_owner_agent(s, dev, "dy")
        a0 = (s.query(service.Agent)
              .filter(service.Agent.user_id == dev).first())
        from agentboard.features.identity.service import create_api_key
        _i0, k0 = create_api_key(s, user_id=dev, name="key-a0",
                                 permissions=["api:read", "api:write"],
                                 agent_ref=a0.agent_id)
        st = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = st.id
        s.commit()
    mq_events, wh_events = [], []
    p1, p2 = _patch_events(mq_events, wh_events)
    with p1, p2:
        for key in (k0, k1, k2):
            _call_api_review_task(t_id, dev, "approve", "ok", key)
    names = [e[0][0] for e in mq_events]
    # Step 4 P1-1（2026-08-10 review）：task 评审用 entity.action 形式
    assert names == [mq.EVENT_TASK_REVIEW_VOTE_CAST, mq.EVENT_TASK_REVIEW_VOTE_CAST,
                     mq.EVENT_TASK_REVIEWED], names
    # 三票都落库（per-agent 一票，未被 user 维度去重）
    with SessionLocal() as s:
        assert _votes(s, "task", t_id) == (0, 0, 0)   # 结算后清票
        assert s.get(service.Task, t_id).status == "done"
    # Webhook 通道与 MQ 事件同构（_notify_webhooks(s, project_id, event, payload)）
    wh_names = [e[0][2] for e in wh_events]
    assert wh_names == [mq.EVENT_TASK_REVIEW_VOTE_CAST, mq.EVENT_TASK_REVIEW_VOTE_CAST,
                        mq.EVENT_TASK_REVIEWED]


def test_api_task_rejected_event(seeded, monkeypatch):
    """Task 结算 reject → task.rejected（MQ + Webhook 双通道断言）。

    T1.1 改写：三票来自 owner 名下三个 agent（1 approve : 2 reject → 多数驳回）。
    """
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    pid, dev, r1, r2, r3, epic_id = seeded
    with SessionLocal() as s:
        _a1, k1 = _add_owner_agent(s, dev, "rx")
        _a2, k2 = _add_owner_agent(s, dev, "ry")
        a0 = (s.query(service.Agent)
              .filter(service.Agent.user_id == dev).first())
        from agentboard.features.identity.service import create_api_key
        _i0, k0 = create_api_key(s, user_id=dev, name="key-r0",
                                 permissions=["api:read", "api:write"],
                                 agent_ref=a0.agent_id)
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)
        t_id = t.id
        s.commit()
    mq_events, wh_events = [], []
    p1, p2 = _patch_events(mq_events, wh_events)
    with p1, p2:
        for key, verdict in ((k0, "approve"), (k1, "reject"), (k2, "reject")):
            _call_api_review_task(t_id, dev, verdict, "c", key)
    names = [e[0][0] for e in mq_events]
    # Step 4 P1-1（2026-08-10 review）：task 评审用 entity.action 形式
    assert names == [mq.EVENT_TASK_REVIEW_VOTE_CAST, mq.EVENT_TASK_REVIEW_VOTE_CAST,
                     mq.EVENT_TASK_REJECTED], names
    wh_names = [e[0][2] for e in wh_events]
    assert wh_names == [mq.EVENT_TASK_REVIEW_VOTE_CAST, mq.EVENT_TASK_REVIEW_VOTE_CAST,
                        mq.EVENT_TASK_REJECTED]
    with SessionLocal() as s:
        t2 = s.get(service.Task, t_id)
        assert t2.status == "in_progress"
        assert t2.review_round == 1


# ---------- 10. Epic 97 AST 护栏：mcp_server.py 零 _api( 残留 ----------

def test_mcp_ast_no_legacy_api():
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = [n for n in ast.walk(tree)
           if isinstance(n, ast.Name) and n.id == "_api"]
    assert bad == [], f"mcp_server.py 残留 {len(bad)} 处 _api( 调用（Epic 97 护栏）"
