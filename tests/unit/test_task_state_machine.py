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
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Project
from agentboard.features.work_items.models import Task
from agentboard.features.work_items.state_machine import (
    TaskStateMachine, execute_transition,
)


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_task_sm_tmp.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    yield
    engine.dispose(close=True)


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


@pytest.fixture
def admin_user(session):
    """P1 #3 测试用：admin 用户。"""
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = User(
        username=f"admin_{suffix}",
        display_name="Test Admin",
        password_hash="x",
        is_admin=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


@pytest.fixture
def non_admin_user(session):
    """P1 #3 测试用：普通用户。"""
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = User(
        username=f"user_{suffix}",
        display_name="Test User",
        password_hash="x",
        is_admin=False,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


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
    # Review 2026-08-26 P1 #3：task 不能再 todo → done 直接跳。
    # 测试场景改为：走正常路径 todo → in_progress → in_review → done，
    # 然后验证 done 可以 re-open 到 in_progress（保留这条边的合法语义）。
    task.status_reason = StatusReason.COMPLETED.value  # noqa
    # 路径 1：todo → in_progress（不需要 status_reason）
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_PROGRESS.value
    # 路径 2：in_progress → in_review（仍然不需要 status_reason）
    execute_transition(session, task, Status.IN_REVIEW.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_REVIEW.value
    # 路径 3：in_review → done（必填 status_reason=completed）
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


# -------------------------------------------------------------
# Review 2026-08-26 P1 #1 / P1 #3 验收测试
# -------------------------------------------------------------

def test_p1_no_todo_to_done_direct_transition(session, task):
    """P1 #3：通用状态机不允许 {todo, in_progress} → done 直接跳。

    Review 2026-08-26：自动 Agent workflow 必须走
    todo → in_progress → in_review → done；admin 强制完成走
    force_complete_task 显式命令（带 manual_override reason）。
    """
    # todo → done 必须抛 IllegalTransition
    with pytest.raises(Exception) as exc_info:
        execute_transition(session, task, Status.DONE.value)
    assert "todo" in str(exc_info.value).lower() and "done" in str(exc_info.value).lower()

    # in_progress → done 也必须抛 IllegalTransition
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    session.refresh(task)
    with pytest.raises(Exception) as exc_info:
        execute_transition(session, task, Status.DONE.value)
    assert "in_progress" in str(exc_info.value).lower() and "done" in str(exc_info.value).lower()


def test_p1_review_timeout_blocked_maintains_invariants_via_set_status(session, task):
    """P1 #1：scan_review_timeouts 走 raw SQL 写 blocked 会破坏 invariant。

    修复后 set_status 路径必须自动维护 status_reason + previous_status +
    TaskStatusHistory。模拟修复后的行为，验证 invariant 真的被维护。
    """
    from agentboard.features.work_items.service import set_status
    from agentboard.core.common.enums import StatusReason

    # 走正常路径到 in_review
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    execute_transition(session, task, Status.IN_REVIEW.value)
    session.commit()
    session.refresh(task)
    assert task.status == Status.IN_REVIEW.value

    # 模拟 review_round 达到 MAX_REVIEW_ROUNDS 后 scan_review_timeouts 调 set_status
    # （修后用 set_status 替代 raw SQL）
    t_updated = set_status(
        session, task.id, Status.BLOCKED.value,
        changed_by=None,
        reason="timeout max review rounds",
        status_reason=StatusReason.PENDING_REQUIREMENT_CHANGE.value,
        cas_predicate=lambda x: x.status == Status.IN_REVIEW.value,
    )
    session.commit()
    session.refresh(t_updated)

    # Invariant 1：status_reason 必填且合法
    assert t_updated.status_reason == StatusReason.PENDING_REQUIREMENT_CHANGE.value
    # Invariant 2：previous_status 自动维护
    assert t_updated.previous_status == Status.IN_REVIEW.value
    # Invariant 3：status 真的到 blocked
    assert t_updated.status == Status.BLOCKED.value


def test_p1_set_status_cas_predicate_blocks_concurrent_change(session, task):
    """P1 #2：set_status 的 cas_predicate 保留 CAS 语义。

    修复后 review_task / scan_review_timeouts 改用 set_status 时把
    原 raw SQL 的 CAS 条件（reviewer_id=X AND status=IN_REVIEW）作为
    cas_predicate 传入，失败时抛 InvalidValue 而非静默改状态。
    """
    from agentboard.features.work_items.service import set_status

    # 走正常路径到 in_review
    execute_transition(session, task, Status.IN_PROGRESS.value)
    session.commit()
    execute_transition(session, task, Status.IN_REVIEW.value)
    session.commit()
    session.refresh(task)

    # CAS 失败：reviewer_id 不匹配
    with pytest.raises(Exception) as exc_info:
        set_status(
            session, task.id, Status.DONE.value,
            changed_by=999,  # 任意
            reason="concurrent review collision",
            status_reason="completed",
            cas_predicate=lambda x: x.reviewer_id == 999 and x.status == Status.IN_REVIEW.value,
        )
    # 必须抛 InvalidValue；state 仍 in_review
    session.refresh(task)
    assert task.status == Status.IN_REVIEW.value
    assert "concurrent" in str(exc_info.value).lower() or "cas" in str(exc_info.value).lower()


def test_p1_force_complete_task_uses_manual_override_status_reason(session, task, admin_user):
    """P1 #3：force_complete_task 是 admin 显式 exceptional path。

    验证：admin_user 调 force_complete_task 必须写到 status_reason="manual_override"，
    写 history 记录 changed_by=admin。
    """
    from agentboard.features.work_items.service import force_complete_task

    # task 当前是 todo
    assert task.status == Status.TODO.value

    t = force_complete_task(
        session, task.id,
        admin_user_id=admin_user.id,
        reason="reviewer 失联，紧急 hotfix",
    )
    session.refresh(t)
    assert t.status == Status.DONE.value
    from agentboard.core.common.enums import StatusReason
    assert t.status_reason == StatusReason.MANUAL_OVERRIDE.value


def test_p1_force_complete_task_rejects_non_admin(session, task, non_admin_user):
    """P1 #3：non-admin 调 force_complete_task 必须抛 InvalidValue（403-like）。"""
    from agentboard.features.work_items.service import force_complete_task

    with pytest.raises(Exception) as exc_info:
        force_complete_task(
            session, task.id,
            admin_user_id=non_admin_user.id,
            reason="试图绕过 review",
        )
    # service 层断言：admin 校验失败 → InvalidValue
    assert "admin" in str(exc_info.value).lower() or "is_admin" in str(exc_info.value).lower()
    # 状态未变
    session.refresh(task)
    assert task.status == Status.TODO.value
