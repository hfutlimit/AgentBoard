"""Story 265 验收测试：Task 状态精简（5 状态集 + status_reason + 4 类型）。

覆盖：
- enums: Status 仅 5 值；ItemType 仅 4 值；StatusReason 6 值
- models: tasks.status_reason 列存在；CheckConstraint 已收紧
- service.set_status: status_reason 校验（done/blocked 必填、离开自动清空、非法值拒绝）
- service.claim_development_task: 仅认领 todo（backlog 已下线）
- service.batch_update_task_status: status_reason 必填校验
- 状态机: 5 状态表（todo→in_progress→in_review→done；blocked 全向可达）
- 类型迁移: task→dev、test_execution→qa；design/bug 保留
- 迁移: status_reason 列存在；旧值已不存在
"""
import os
import sys
import tempfile

# 必须在导入 agentboard 之前设置独立临时 SQLite，避免落到工作目录脏库
# （与其他 tests/test_*.py 的统一模式保持一致）。
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"

# 与其它多文件批量跑时的隔离模式一致：del 已加载的 agentboard 模块，
# 让本文件 import 时用上面刚设置的 DB URL 重新加载（否则共享上一文件的模块）。
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import database, service
from agentboard.database import SessionLocal, init_db
from agentboard.domains.common.enums import (
    ALL_STATUSES, ALL_STATUS_REASONS, ALL_TYPES,
    ItemType, Status, StatusReason, STATUS_REASONS_BY_STATUS,
)
from agentboard.domains.work_items.models import Task


# ---------- enums 校验 ----------

def test_status_enum_has_exactly_5_values():
    """Status 枚举仅 5 值。"""
    assert set(ALL_STATUSES) == {
        Status.TODO, Status.IN_PROGRESS, Status.IN_REVIEW,
        Status.DONE, Status.BLOCKED,
    }
    # 旧值已下线
    for old in ("backlog", "in_design", "design_pending_review",
                "design_review_approved", "verifying", "final_review"):
        assert old not in {str(s) for s in ALL_STATUSES}


def test_item_type_has_exactly_4_values():
    """ItemType 枚举仅 4 值。"""
    assert set(ALL_TYPES) == {ItemType.DEV, ItemType.BUG, ItemType.QA, ItemType.DESIGN}
    # 旧值已下线
    for old in ("task", "test_execution"):
        assert old not in {str(t) for t in ALL_TYPES}


def test_status_reason_enum_has_7_values():
    """StatusReason 枚举 7 值（5 个用户可选项 + completed + withdrawn + legacy 迁移专用）。"""
    assert set(ALL_STATUS_REASONS) == {
        StatusReason.COMPLETED, StatusReason.WITHDRAWN,
        StatusReason.BLOCKED_BY_OTHER_TICKET, StatusReason.PENDING_REQUIREMENT_CHANGE,
        StatusReason.OUT_OF_SCOPE, StatusReason.DUPLICATE,
        StatusReason.LEGACY,  # 迁移专用：历史 blocked 数据无明确原因
    }


def test_status_reasons_by_status_mapping():
    """STATUS_REASONS_BY_STATUS：done/blocked 各自允许值。"""
    assert STATUS_REASONS_BY_STATUS[str(Status.DONE)] == {
        StatusReason.COMPLETED, StatusReason.WITHDRAWN,
    }
    # blocked 含 4 个用户选项 + 1 个迁移遗留 legacy（UI 可筛选让用户重选）
    assert STATUS_REASONS_BY_STATUS[str(Status.BLOCKED)] == {
        StatusReason.BLOCKED_BY_OTHER_TICKET, StatusReason.PENDING_REQUIREMENT_CHANGE,
        StatusReason.OUT_OF_SCOPE, StatusReason.DUPLICATE,
        StatusReason.LEGACY,
    }


# ---------- models 校验 ----------

def test_task_model_has_status_reason_column():
    """tasks 表新增 status_reason 列。"""
    cols = {c.name for c in Task.__table__.columns}
    assert "status_reason" in cols


def test_task_model_check_constraints_use_new_values():
    """CheckConstraint 已收紧为 4 类型 / 5 状态。"""
    constraints = {c.name: str(c.sqltext) for c in Task.__table__.constraints
                   if hasattr(c, "sqltext")}
    assert "ck_tasks_type" in constraints
    assert "ck_tasks_status" in constraints
    assert "dev" in constraints["ck_tasks_type"]
    assert "qa" in constraints["ck_tasks_type"]
    assert "backlog" not in constraints["ck_tasks_status"]
    assert "in_design" not in constraints["ck_tasks_status"]


# ---------- service 业务校验 ----------

@pytest.fixture
def session():
    """独立 SQLite 内存库测试。"""
    init_db()
    s = SessionLocal()
    yield s
    s.close()


def _make_user_and_project(s, name="u1", proj="p1"):
    # 用 uuid 后缀保证跨用例唯一
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"{name}_{suffix}", password="password123")
    p = service.create_project(s, name=f"{proj}_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E-{suffix}")
    st = service.create_story(s, epic_id=e.id, title=f"S-{suffix}")
    return u, p, st


def test_set_status_done_requires_status_reason(session):
    """set_status 到 done 必传 status_reason。"""
    u, p, st = _make_user_and_project(session)
    st = service.create_story(session, epic_id=service.create_epic(session, project_id=p.id, title="E1").id,
                              title="S1")
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T1")
    # 先推进到 in_progress
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    # done 不传 reason → 抛错
    with pytest.raises(service.InvalidValue):
        service.set_status(session, t.id, Status.DONE, changed_by=u.id)
    # 传非法值 → 抛错
    with pytest.raises(service.InvalidValue):
        service.set_status(session, t.id, Status.DONE, changed_by=u.id,
                           status_reason="not_a_real_reason")
    # 传合法值 → 成功
    t2 = service.set_status(session, t.id, Status.DONE, changed_by=u.id,
                            status_reason=StatusReason.COMPLETED)
    assert t2.status == Status.DONE
    assert t2.status_reason == StatusReason.COMPLETED


def test_set_status_blocked_requires_status_reason(session):
    """set_status 到 blocked 必传 status_reason。"""
    u, p, st = _make_user_and_project(session)
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T1")
    with pytest.raises(service.InvalidValue):
        service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id)
    t2 = service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                            status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET)
    assert t2.status_reason == StatusReason.BLOCKED_BY_OTHER_TICKET


def test_set_status_clears_status_reason_on_other_statuses(session):
    """离开 done/blocked 或去非 done/blocked 状态时，status_reason 应清空。"""
    u, p, st = _make_user_and_project(session)
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T1")
    # → done
    service.set_status(session, t.id, Status.DONE, changed_by=u.id,
                       status_reason=StatusReason.WITHDRAWN)
    # re-open done → in_progress，reason 应清空
    t2 = service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    assert t2.status == Status.IN_PROGRESS
    assert t2.status_reason is None


def test_claim_only_accepts_todo(session):
    """claim_development_task 仅认领 todo（旧 backlog 已下线）。"""
    u, p, st = _make_user_and_project(session)
    # 旧值 in_progress 不应可认领
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T1")
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    with pytest.raises(service.InvalidValue):
        service.claim_development_task(session, t.id, user_id=u.id)
    # 新建 todo task 应可认领
    t2 = service.create_task(session, project_id=p.id, story_id=st.id, title="T2")
    t3 = service.claim_development_task(session, t2.id, user_id=u.id)
    assert t3.status == Status.IN_PROGRESS
    assert t3.assignee_id == u.id


def test_5_state_transitions(session):
    """5 状态机合法迁移表。"""
    transitions = service.TRANSITIONS
    # 验证表只含 5 状态
    assert set(transitions.keys()) == {
        Status.TODO, Status.IN_PROGRESS, Status.IN_REVIEW,
        Status.DONE, Status.BLOCKED,
    }
    # todo 起点
    assert Status.IN_PROGRESS in transitions[Status.TODO]
    assert Status.DONE in transitions[Status.TODO]
    # in_progress 可到 in_review / done
    assert Status.IN_REVIEW in transitions[Status.IN_PROGRESS]
    assert Status.DONE in transitions[Status.IN_PROGRESS]
    # in_review 可到 done（评审通过）
    assert Status.DONE in transitions[Status.IN_REVIEW]
    # done 可 re-open
    assert Status.IN_PROGRESS in transitions[Status.DONE]


def test_blocked_is_reachable_from_any_status(session):
    """任意非终态 → blocked 全向可达（set_status 特判保留）。"""
    u, p, st = _make_user_and_project(session)
    # todo
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T-todo")
    t2 = service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                            status_reason=StatusReason.DUPLICATE)
    assert t2.status == Status.BLOCKED
    assert t2.previous_status == Status.TODO
    # in_progress
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T-inprogress")
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    t2 = service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                            status_reason=StatusReason.DUPLICATE)
    assert t2.status == Status.BLOCKED
    assert t2.previous_status == Status.IN_PROGRESS
    # in_review
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T-inreview")
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t.id, Status.IN_REVIEW, changed_by=u.id)
    t2 = service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                            status_reason=StatusReason.DUPLICATE)
    assert t2.status == Status.BLOCKED
    assert t2.previous_status == Status.IN_REVIEW


def test_type_field_uses_4_values(session):
    """create_task 默认 type=dev，type 字段接受 4 值。"""
    u, p, st = _make_user_and_project(session)
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T-dev")
    assert t.type == ItemType.DEV
    t2 = service.create_task(session, project_id=p.id, story_id=st.id, title="T-bug", type=ItemType.BUG)
    assert t2.type == ItemType.BUG
    t3 = service.create_task(session, project_id=p.id, story_id=st.id, title="T-qa", type=ItemType.QA)
    assert t3.type == ItemType.QA
    t4 = service.create_task(session, project_id=p.id, story_id=st.id, title="T-design", type=ItemType.DESIGN)
    assert t4.type == ItemType.DESIGN


def test_old_type_values_rejected_by_check_constraint(session):
    """旧 type 值（task/test_execution）应被 CheckConstraint 拒绝。"""
    from sqlalchemy.exc import IntegrityError
    t = Task(project_id=1, story_id=None, type="task", title="bad", status=Status.TODO)
    session.add(t)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_old_status_values_rejected_by_check_constraint(session):
    """旧 status 值（backlog/in_design 等）应被 CheckConstraint 拒绝。"""
    from sqlalchemy.exc import IntegrityError
    t = Task(project_id=1, story_id=None, type=ItemType.DEV, title="bad", status="backlog")
    session.add(t)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_batch_update_task_status_validates_status_reason(session):
    """batch_update_task_status 校验 status_reason。"""
    u, p, st = _make_user_and_project(session)
    t1 = service.create_task(session, project_id=p.id, story_id=st.id, title="T1")
    t2 = service.create_task(session, project_id=p.id, story_id=st.id, title="T2")
    # 不传 reason 批量改 done → InvalidValue（顶层校验）
    with pytest.raises(service.InvalidValue):
        service.batch_update_task_status(
            session, [t1.id, t2.id], Status.DONE, changed_by=u.id,
        )
    # 传非法 reason → 抛错
    with pytest.raises(service.InvalidValue):
        service.batch_update_task_status(
            session, [t1.id, t2.id], Status.DONE, changed_by=u.id,
            status_reason="not_real",
        )
    # 传合法 reason → 成功
    result2 = service.batch_update_task_status(
        session, [t1.id, t2.id], Status.DONE, changed_by=u.id,
        status_reason=StatusReason.COMPLETED,
    )
    assert set(result2["updated"]) == {t1.id, t2.id}
    # 验证 status_reason 已落库
    s1 = session.get(Task, t1.id)
    s2 = session.get(Task, t2.id)
    assert s1.status_reason == StatusReason.COMPLETED
    assert s2.status_reason == StatusReason.COMPLETED


def test_complete_sprint_clears_blocked_residue(session):
    """complete_sprint 直接 SQL UPDATE 必须清 status_reason/previous_status，
    否则 blocked 任务退回后会残留「status=todo + status_reason=blocked_reason」
    的不一致状态，违反「非 done/blocked 必清 reason」业务规则。
    """
    u, p, st = _make_user_and_project(session)
    # 建一个 sprint + 一个 blocked task
    sprint = service.create_sprint(session, project_id=p.id, title="S1")
    t = service.create_task(session, project_id=p.id, story_id=st.id, title="T-blocked",
                            sprint_id=sprint.id)
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                       status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET)
    # 验证 set_status 写入 previous_status
    t_before = session.get(Task, t.id)
    assert t_before.status == Status.BLOCKED
    assert t_before.previous_status == Status.IN_PROGRESS
    assert t_before.status_reason == StatusReason.BLOCKED_BY_OTHER_TICKET
    # 完成 sprint
    service.complete_sprint(session, sprint.id)
    # 验证 task 退回 todo 时 status_reason / previous_status 都被清空
    t_after = session.get(Task, t.id)
    assert t_after.status == Status.TODO
    assert t_after.status_reason is None, (
        f"complete_sprint should clear status_reason; got {t_after.status_reason}"
    )
    assert t_after.previous_status is None, (
        f"complete_sprint should clear previous_status; got {t_after.previous_status}"
    )
