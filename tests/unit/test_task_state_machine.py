"""Task 状态机单元测试。

覆盖 Story 265 收敛后的 5 状态集 + blocked 全向/解除恢复 + status_reason 校验。
"""
import os
# 必须在 import engine 之前设置(否则会用默认 sqlite:///./agentboard.db)
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_task_sm_tmp.db"

import sys
import pytest

from agentboard.core.common.enums import Status, StatusReason
from agentboard.core.exceptions import IllegalTransition, InvalidValue
from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.projects.models import Project
from agentboard.features.work_items.models import Task
from agentboard.features.work_items.state_machine import (
    TaskStateMachine, execute_transition,
)


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    init_db()
    yield
    engine.dispose()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def task(session):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    p = Project(name=f"sm-test-{suffix}", key=f"SM{suffix}", description="")
    session.add(p)
    session.flush()
    t = Task(project_id=p.id, title="t", status=Status.TODO.value, status_reason=None)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_todo_to_in_progress(session, task):
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_PROGRESS.value
    assert task.previous_status is None
    assert task.status_reason is None


def test_in_progress_to_in_review(session, task):
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    execute_transition(session, task, Status.IN_REVIEW.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_REVIEW.value


def test_in_review_to_done_requires_reason(session, task):
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    execute_transition(session, task, Status.IN_REVIEW.value)
    session.commit()
    # done 必须有 status_reason
    task.status_reason = StatusReason.COMPLETED.value
    session.commit()
    execute_transition(session, task, Status.DONE.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.DONE.value
    assert task.status_reason == StatusReason.COMPLETED.value


def test_blocked_is_reachable_from_any_state(session, task):
    """blocked 全向可达:任意状态 → blocked。"""
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.BLOCKED.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.BLOCKED.value
    assert task.previous_status == Status.IN_PROGRESS.value


def test_unblock_restores_previous_status(session, task):
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.BLOCKED.value)
    session.commit()
    # 解除 blocked → 回到 previous_status (in_progress)
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_PROGRESS.value
    assert task.previous_status is None  # 解除时清空


def test_done_can_reopen_to_in_progress(session, task):
    task.status_reason = StatusReason.COMPLETED.value
    session.commit()
    execute_transition(session, task, Status.DONE.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.DONE.value
    # re-open
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_PROGRESS.value
    # re-open 时 status_reason 应被清空(done 状态之外 → None)
    assert task.status_reason is None


def test_illegal_transition_raises(session, task):
    """todo → in_review 不在迁移表里,抛 IllegalTransition。"""
    with pytest.raises(IllegalTransition):
        execute_transition(session, task, Status.IN_REVIEW.value)


def test_blocked_requires_status_reason(session, task):
    """进入 blocked 必须有合法 status_reason。"""
    # task.status_reason 默认 None → 校验失败抛 InvalidValue
    with pytest.raises(InvalidValue):
        execute_transition(session, task, Status.BLOCKED.value)
    # 设上合法 reason 后通过
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.BLOCKED.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.BLOCKED.value
    assert task.previous_status == Status.TODO.value
