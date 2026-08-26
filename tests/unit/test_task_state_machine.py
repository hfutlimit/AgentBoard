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


@pytest.fixture(autouse=True)
def _isolate_task_sm_db(monkeypatch):
    """重写 AGENTBOARD_DB_URL,防止其他 test 文件 module-load 时
    覆盖 (test_story_status_machine.py 设了 tempfile,mock 顺序敏感)。
    """
    monkeypatch.setenv("AGENTBOARD_DB_URL", "sqlite:///./_test_task_sm_tmp.db")


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


# ---- unblock 4 目标覆盖(2026-08-14 修复放宽) ------------------------------
# blocked → 任意 {todo, in_progress, in_review, done} 都允许,
# 不强制回到 previous_status。previous_status 字段仅作 UI 推荐默认值。

@pytest.mark.parametrize("target", [
    Status.TODO, Status.IN_PROGRESS, Status.IN_REVIEW, Status.DONE,
])
def test_unblock_allows_any_of_4_targets(session, task, target):
    """in_progress → blocked → unblock to {todo, in_progress, in_review, done}
    全部允许(都不需要与 previous_status 匹配)。
    """
    # 进 in_progress
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    # 进 blocked
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.BLOCKED.value)
    session.commit()
    session.refresh(task)
    assert task.previous_status == Status.IN_PROGRESS.value
    # unblock 到 target(可能与 previous_status 不同)
    if target == Status.DONE:
        task.status_reason = StatusReason.COMPLETED.value
        session.commit()
    execute_transition(session, task, target.value)
    session.commit()
    session.refresh(task)
    assert task.status == target.value
    # 出 blocked 后 previous_status 清空
    assert task.previous_status is None


def test_unblock_error_message_lists_allowed_targets(session, task):
    """超出 4 目标的 unblock 应抛 IllegalTransition,错误信息列出允许的目标,
    而不是说 'only previous_status targets are allowed'(误导)。

    用手动 try/except 而非 pytest.raises:在多文件 pytest run 下,
    pytest.raises context manager 在 IllegalTransition 跨模块抛出时
    偶发不能 catch(IllegalTransition 来自 state_machine.py 的局部
    from import,与本测试文件顶层 import 的同名类在 pytest collection
    期间被重绑过),手动 try/except 不受影响。
    """
    from sqlalchemy import text as sql_text
    # 强制置 blocked(绕过 SM 准备)
    session.execute(sql_text("UPDATE tasks SET status='blocked' WHERE id=:id"),
                    {"id": task.id})
    session.commit()
    session.refresh(task)
    # 试图 unblock 到不在 4 目标里的状态(未知状态)
    raised = None
    try:
        execute_transition(session, task, "totally_made_up_status")
    except Exception as e:
        if "IllegalTransition" in type(e).__name__:
            raised = e
        else:
            raise
    assert raised is not None, "expected IllegalTransition to be raised"
    msg = str(raised)
    # 不应再说"only previous_status targets are allowed"
    assert "only previous_status targets are allowed" not in msg, (
        f"误导信息应已删除,实际: {msg!r}"
    )
    # 错误信息应该列出允许的目标
    for t in ("todo", "in_progress", "in_review", "done"):
        assert t in msg, f"expected {t!r} in error message: {msg!r}"


def test_unblock_to_non_previous_status_writes_history(session, task):
    """in_progress → blocked → unblock to TODO(非 previous_status):
    history 应正确记录 in_progress→blocked→todo 三段变迁。"""
    from agentboard.features.work_items.models import TaskStatusHistory
    # 进 in_progress
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    # 进 blocked
    task.status_reason = StatusReason.LEGACY.value
    session.commit()
    execute_transition(session, task, Status.BLOCKED.value)
    session.commit()
    # unblock to TODO(非 previous_status)
    execute_transition(session, task, Status.TODO.value)
    session.commit()
    # 验证 history 链
    hist = (session.query(TaskStatusHistory)
            .filter(TaskStatusHistory.task_id == task.id)
            .order_by(TaskStatusHistory.id.asc()).all())
    transitions = [(h.from_status, h.to_status) for h in hist]
    # 应有 todo→in_progress, in_progress→blocked, blocked→todo
    assert (Status.TODO.value, Status.IN_PROGRESS.value) in transitions
    assert (Status.IN_PROGRESS.value, Status.BLOCKED.value) in transitions
    assert (Status.BLOCKED.value, Status.TODO.value) in transitions


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
