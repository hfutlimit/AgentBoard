"""Regression tests for ``scheduling.service.reclaim_stale_tasks``.

2026-09-07 (#1716 后续收口)。两件事：

1. 回收必须**释放分配**。老实现只把 ``assignee_id`` 清掉，留着
   ``current_assignment_id`` 指向一条仍然 ``active`` 的 ``TaskAssignment``。
   于是回收把任务推进了「todo + FK 非空 + active_slot 被占」的死结：
   所有 claim/apply/arbitrate 路径都要求 FK 为 NULL
   （``try_assign_task``），而 ``uq_task_assignment_active_slot``
   又挡住新的 active 分配 —— 回收本身制造了当初需要 admin
   force-clear 才能解开的状态。
2. 回收必须覆盖 arbitrate 派发。apply → arbitrate 把任务推到
   in_progress 但**不写租约列**（``claimed_by`` / ``claimed_at``），
   MQ-less 部署里这条路径没有任何超时：worker 掉了就永久卡住，
   并且会被 ``coordinator._poll_assigned_in_progress`` 每轮重投。
   人工路径（``manual`` / ``schedule``）依旧受保护。
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_reclaim_release_tmp.db"

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from agentboard.core.common.enums import Status
from agentboard.core.common.models import utc_now
from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import engine
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Agent as AgentRow, Project
from agentboard.features.scheduling import service as scheduling_service
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.features.work_items import service as work_items_service
from agentboard.features.work_items.models import Task


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_reclaim_release_tmp.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    _database.reset_engine()
    # create_all（不是 alembic）：这条回归要验的正是模型约束/行为。
    from agentboard.core.common.models import Base
    from agentboard.features.identity import models as _id_models  # noqa: F401
    from agentboard.features.projects import models as _proj_models  # noqa: F401
    from agentboard.features.scheduling import models as _sched_models  # noqa: F401
    from agentboard.features.work_items import models as _wi_models  # noqa: F401
    Base.metadata.create_all(bind=_database.engine)
    yield
    engine.dispose(close=True)


@pytest.fixture
def session():
    s = _database.SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def admin_user(session):
    u = User(username=f"rc-admin-{uuid.uuid4().hex[:8]}",
             password_hash="x", is_admin=True)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


@pytest.fixture
def project(session):
    suffix = uuid.uuid4().hex[:8]
    p = Project(name=f"rc-{suffix}", key=f"RC{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def agent(session, admin_user):
    a = AgentRow(
        agent_id=f"rc-agent-{uuid.uuid4().hex[:6]}", name="rc-agent",
        user_id=admin_user.id, cli_command="codebuddy", model="m",
        capabilities="[]",
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _make_assigned_task(session, project, agent, *, source, title,
                        user_id, stale: bool, claimed_by: str | None = None):
    """in_progress Task with an active TaskAssignment (arbitration/claim/manual)."""
    t = Task(
        project_id=project.id, title=title, type="design",
        status=Status.IN_PROGRESS.value, assignment_mode="arbitrated",
        assignee_id=user_id, owner_user_id=user_id,
    )
    session.add(t)
    session.flush()
    a = TaskAssignment(
        task_id=t.id, agent_registry_id=agent.id, user_id=user_id,
        source=source, status="active", active_slot="active",
    )
    session.add(a)
    session.flush()
    t.current_assignment_id = a.id
    if claimed_by is not None:
        t.claimed_by = claimed_by
        t.claimed_at = utc_now() - timedelta(hours=2)
    session.commit()
    if stale:
        # 直接把 updated_at 推到过去（等价于「认领后再无任何动静」），
        # 之后不再 flush 该对象，免得 onupdate 把它刷回 now。
        session.execute(
            update(Task).where(Task.id == t.id)
            .values(updated_at=utc_now() - timedelta(hours=2))
        )
        session.commit()
        session.expire_all()
    session.refresh(t)
    session.refresh(a)
    return t, a


def test_reclaim_releases_assignment_and_clears_fk(session, project, agent, admin_user):
    """arbitrate 派发且静默超期 → 回收，且分配被释放、FK 被清。"""
    t, a = _make_assigned_task(
        session, project, agent, source="arbitration",
        title="stale-arbitrated", user_id=admin_user.id, stale=True,
    )
    reclaimed = scheduling_service.reclaim_stale_tasks(session, lease_seconds=1800)
    assert t.id in reclaimed

    session.refresh(t)
    session.refresh(a)
    assert t.status == Status.TODO.value
    assert t.assignee_id is None
    assert t.current_assignment_id is None, "回收必须清 FK，否则任务永久不可再认领"
    assert a.status == "released"
    assert a.active_slot is None, "active_slot 必须让位，否则新分配撞唯一索引"
    assert a.completed_at is not None

    history = work_items_service.list_task_status_history(session, t.id)
    assert any("租约到期回收" in (row.reason or "") for row in history)


def test_reclaimed_task_can_be_claimed_again(session, project, agent, admin_user):
    """回收的价值：同一任务能重新分配，不需要 admin force-clear。"""
    t, _ = _make_assigned_task(
        session, project, agent, source="arbitration",
        title="reclaimable", user_id=admin_user.id, stale=True,
    )
    assert scheduling_service.reclaim_stale_tasks(session, lease_seconds=1800)

    updated, new_assignment = work_items_service.try_assign_task(
        session, t.id, user_id=admin_user.id,
        agent_registry_id=agent.id, source="arbitration",
    )
    assert updated.status == Status.IN_PROGRESS.value
    assert updated.current_assignment_id == new_assignment.id
    assert new_assignment.status == "active"
    assert new_assignment.active_slot == "active"
    # 同一 task 上允许「旧 released + 新 active」共存
    assert session.query(TaskAssignment).filter(
        TaskAssignment.task_id == t.id).count() == 2


def test_claimed_lease_expiry_still_reclaimed(session, project, agent, admin_user):
    """既有行为不变：写了租约列的 agent 认领行照旧回收。"""
    t, a = _make_assigned_task(
        session, project, agent, source="claim", title="stale-claim",
        user_id=admin_user.id, stale=True, claimed_by="worker-1",
    )
    assert t.id in scheduling_service.reclaim_stale_tasks(session, lease_seconds=1800)
    session.refresh(t)
    session.refresh(a)
    assert t.status == Status.TODO.value
    assert t.current_assignment_id is None
    assert a.status == "released"


def test_fresh_activity_is_protected(session, project, agent, admin_user):
    """updated_at 还在窗口内 = 仍在活跃流转，一律不动。"""
    t, a = _make_assigned_task(
        session, project, agent, source="arbitration",
        title="still-running", user_id=admin_user.id, stale=False,
    )
    assert scheduling_service.reclaim_stale_tasks(session, lease_seconds=1800) == []
    session.refresh(t)
    session.refresh(a)
    assert t.status == Status.IN_PROGRESS.value
    assert t.current_assignment_id == a.id
    assert a.status == "active"


def test_human_manual_assignment_without_lease_is_not_reclaimed(
    session, project, agent, admin_user,
):
    """人工路径不能被自动回收（原有保护语义保持不变）。"""
    t, a = _make_assigned_task(
        session, project, agent, source="manual", title="human-held",
        user_id=admin_user.id, stale=True,
    )
    assert scheduling_service.reclaim_stale_tasks(session, lease_seconds=1800) == []
    session.refresh(t)
    session.refresh(a)
    assert t.status == Status.IN_PROGRESS.value
    assert t.current_assignment_id == a.id
    assert a.status == "active"


def test_in_progress_without_assignment_is_untouched(session, project, admin_user):
    """没有任何 agent 分配的 in_progress（人工直接 set_status）不受影响。"""
    t = Task(
        project_id=project.id, title="human-in-progress", type="design",
        status=Status.IN_PROGRESS.value, assignment_mode="claim",
        assignee_id=admin_user.id,
    )
    session.add(t)
    session.commit()
    session.execute(
        update(Task).where(Task.id == t.id)
        .values(updated_at=utc_now() - timedelta(hours=2))
    )
    session.commit()
    assert scheduling_service.reclaim_stale_tasks(session, lease_seconds=1800) == []
    session.refresh(t)
    assert t.status == Status.IN_PROGRESS.value
