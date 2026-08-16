"""Epic 140 切片 1 验收测试：task_outcome 模型 + 过程指标计算 + leaderboard 聚合。

覆盖：
- set_status 到 done/blocked 自动落 outcome（幂等）
- 非终态不落 outcome
- 过程指标：pass_first_try（reject 往返）、review_rounds、attempts、duration_s
- agent_leaderboard 多维聚合（agent × project × task_type）
- list_outcomes 明细
- limit 校验
"""
import os
import sys
import tempfile

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import service
from agentboard.database import SessionLocal, init_db
from agentboard.domains.common.enums import Status, StatusReason
# 注册 learning 模型到 metadata（init_db 走 alembic，此处保证模型可导入）
from agentboard.features.learning import service as ls
from agentboard.features.learning.models import TaskOutcome


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    yield s
    s.close()


def _mk(s, name="u1", proj="p1"):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"{name}_{suffix}", password="password123")
    p = service.create_project(s, name=f"{proj}_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E-{suffix}")
    st = service.create_story(s, epic_id=e.id, title=f"S-{suffix}")
    return u, p, st


def _mk_task(s, u, p, st, assignee=True):
    t = service.create_task(s, project_id=p.id, story_id=st.id, title="T1")
    if assignee:
        t.assignee_id = u.id
        s.commit()
        s.refresh(t)
    return t


def _done(s, t, u, reason=StatusReason.COMPLETED):
    service.set_status(s, t.id, Status.IN_PROGRESS, changed_by=u.id)
    return service.set_status(s, t.id, Status.DONE, changed_by=u.id,
                              status_reason=reason)


# ---------- 落库 ----------

def test_set_status_done_auto_records_outcome(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    row = session.execute(
        __import__("sqlalchemy").select(TaskOutcome).where(TaskOutcome.task_id == t.id)
    ).scalar_one_or_none()
    assert row is not None
    assert row.project_id == p.id
    assert row.agent_id == u.id
    assert row.task_type == "dev"
    assert 0.0 <= row.score <= 1.0
    assert row.attempts >= 1
    assert row.judge_json != "{}"


def test_outcome_idempotent_on_repeat_done(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    # re-open 再 done：应更新而非新增
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t.id, Status.DONE, changed_by=u.id,
                       status_reason=StatusReason.COMPLETED)
    rows = session.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).all()
    assert len(rows) == 1


def test_no_outcome_for_non_terminal(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    rows = session.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).all()
    assert rows == []


def test_blocked_also_records_outcome(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                       status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET)
    row = session.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).one()
    assert row.score <= 1.0


# ---------- 过程指标 ----------

def test_pass_first_try_zero_after_reject(session):
    """in_review 被打回（reject）后再 done → pass_first_try=0，score 显著下降。"""
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)  # 第一次直接 done（无 reject）
    first = session.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).one()
    assert first.score >= 0.9  # 0.4*1 + 0.3*0.75 + 0.2*1 + 0.1*1 = 0.95

    # 第二个任务：先 in_review 再打回 in_progress，重新提交后 done（评审闭环）
    t2 = _mk_task(session, u, p, st)
    service.set_status(session, t2.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t2.id, Status.IN_REVIEW, changed_by=u.id)          # 提交评审
    service.set_status(session, t2.id, Status.IN_PROGRESS, changed_by=u.id)        # reject 打回
    service.set_status(session, t2.id, Status.IN_REVIEW, changed_by=u.id)          # 重新提交
    service.set_status(session, t2.id, Status.DONE, changed_by=u.id,               # 评审通过
                       status_reason=StatusReason.COMPLETED)
    second = session.query(TaskOutcome).filter(TaskOutcome.task_id == t2.id).one()
    assert second.score < first.score
    metrics = __import__("json").loads(second.judge_json)
    assert metrics["pass_first_try"] == 0.0
    assert metrics["rejects"] == 1
    assert metrics["review_rounds"] == 2


def test_withdrawn_reason_quality(session):
    """withdrawn 终态：withdrawn=true、reason_quality=1。"""
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u, reason=StatusReason.WITHDRAWN)
    row = session.query(TaskOutcome).filter(TaskOutcome.task_id == t.id).one()
    metrics = __import__("json").loads(row.judge_json)
    assert metrics["withdrawn"] is True
    assert metrics["reason_quality"] == 1.0


# ---------- 聚合 ----------

def test_agent_leaderboard_aggregation(session):
    u, p, st = _mk(session)
    for i in range(3):
        t = _mk_task(session, u, p, st)
        _done(session, t, u)
    rows = ls.agent_leaderboard(session, project_id=p.id)
    assert len(rows) >= 1
    row = [r for r in rows if r["agent_id"] == u.id][0]
    assert row["tasks"] == 3
    assert 0.0 <= row["avg_score"] <= 1.0
    assert row["task_type"] == "dev"
    assert row["project_id"] == p.id


def test_leaderboard_filters_by_task_type(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    t.type = "bug"
    session.commit()
    _done(session, t, u)
    dev_rows = ls.agent_leaderboard(session, project_id=p.id, task_type="dev")
    bug_rows = ls.agent_leaderboard(session, project_id=p.id, task_type="bug")
    assert all(r["task_type"] == "dev" for r in dev_rows)
    assert any(r["task_type"] == "bug" for r in bug_rows)


def test_leaderboard_limit_validation(session):
    with pytest.raises(service.InvalidValue):
        ls.agent_leaderboard(session, limit=0)
    with pytest.raises(service.InvalidValue):
        ls.agent_leaderboard(session, limit=201)


def test_list_outcomes_detail(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    rows = ls.list_outcomes(session, project_id=p.id)
    assert len(rows) == 1
    assert rows[0]["task_id"] == t.id
    assert "judge_json" in rows[0]
    assert rows[0]["judge_json"]["judge_pending"] is True
