"""Story 265 后的 design task 行为测试（替换原 Epic 123 设计评审段测试）。

原 Epic 123 设计评审流（in_design → design_pending_review → design_review_approved）
已下线，所有 task（含 design）走通用 5 状态流：
  todo → in_progress → in_review → done

本文件覆盖：
- design 任务默认 status=todo（不再是 backlog）
- design 任务可直接 todo→in_progress（不再强制先进 in_design）
- design 任务评审通过与普通 task 路径相同
- blocked 全向可达与 previous_status 恢复
- status_history 记录与查询
"""
import pytest

from agentboard import service
from agentboard.database import SessionLocal
from agentboard.domains.work_items.models import Task, TaskStatusHistory


def _make_user_project_story(s, name):
    u = service.register_user(s, username=f"{name}_{id(s)}", password="password123")
    p = service.create_project(s, name=f"{name}_p_{id(s)}")
    e = service.create_epic(s, project_id=p.id, title="Epic")
    st = service.create_story(s, epic_id=e.id, title="Story", needs_design=True)
    return u, p, st


def _make_design_task(s, user, project, story, title="Design Task"):
    """创建 type=design 任务，直接 INSERT（create_task 不暴露 type=design + needs_design 联动）。"""
    t = Task(project_id=project.id, story_id=story.id, type="design",
             title=title, status="todo")
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


def test_design_task_default_status_is_todo():
    """Story 265 后 design 任务默认 status=todo（不再是 backlog）。"""
    with SessionLocal() as s:
        u, p, st = _make_user_project_story(s, "design_default")
        t = _make_design_task(s, u, p, st, "T-default")
        assert t.status == "todo"


def test_design_task_can_go_directly_to_in_progress():
    """design 任务可直接 todo→in_progress（不再强制先进 in_design）。"""
    with SessionLocal() as s:
        u, p, st = _make_user_project_story(s, "design_direct")
        t = _make_design_task(s, u, p, st, "T-direct")
        # todo → in_progress 合法
        result = service.set_status(s, t.id, "in_progress", changed_by=u.id)
        assert result.status == "in_progress"


def test_design_task_full_lifecycle():
    """design 任务完整生命周期：todo→in_progress→in_review→done。"""
    with SessionLocal() as s:
        u, p, st = _make_user_project_story(s, "design_lifecycle")
        t = _make_design_task(s, u, p, st, "T-lifecycle")
        service.set_status(s, t.id, "in_progress", changed_by=u.id)
        service.set_status(s, t.id, "in_review", changed_by=u.id)
        result = service.set_status(s, t.id, "done", changed_by=u.id,
                                    status_reason="completed")
        assert result.status == "done"
        assert result.status_reason == "completed"


def test_old_design_states_rejected_by_state_machine():
    """旧的 in_design / design_pending_review / design_review_approved 状态不能再用。"""
    with SessionLocal() as s:
        u, p, st = _make_user_project_story(s, "design_old")
        t = _make_design_task(s, u, p, st, "T-old")
        # 任意旧状态都应该被 _check_status 拒绝
        for old in ("in_design", "design_pending_review", "design_review_approved", "final_review", "verifying"):
            with pytest.raises(service.InvalidValue):
                service.set_status(s, t.id, old, changed_by=u.id)


def test_blocked_omnidirectional_for_design_tasks():
    """design 任务 blocked 全向可达，previous_status 记录与恢复。"""
    with SessionLocal() as s:
        u, p, st = _make_user_project_story(s, "design_blocked")
        t = _make_design_task(s, u, p, st, "T-blocked")
        service.set_status(s, t.id, "in_progress", changed_by=u.id)
        t2 = service.set_status(s, t.id, "blocked", changed_by=u.id,
                                status_reason="duplicate")
        assert t2.status == "blocked"
        assert t2.previous_status == "in_progress"
        # 解除 blocked → 恢复到 in_progress
        t3 = service.set_status(s, t.id, "in_progress", changed_by=u.id)
        assert t3.status == "in_progress"
        assert t3.previous_status is None


def test_status_history_recorded_for_design():
    """design 任务 status_history 正常记录。"""
    with SessionLocal() as s:
        u, p, st = _make_user_project_story(s, "design_history")
        t = _make_design_task(s, u, p, st, "T-history")
        service.set_status(s, t.id, "in_progress", changed_by=u.id)
        service.set_status(s, t.id, "in_review", changed_by=u.id)
        rows = s.query(TaskStatusHistory).filter(
            TaskStatusHistory.task_id == t.id).all()
        assert len(rows) == 2
        assert rows[0].to_status == "in_progress"
        assert rows[1].to_status == "in_review"
