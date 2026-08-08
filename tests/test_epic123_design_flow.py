"""Epic 123：状态机扩展——设计评审流 + blocked 全向/历史 + needs_design 分支。

覆盖：
- needs_design=true：todo→in_design 合法；todo→in_progress 非法（须先进设计）；
- needs_design=false：todo→in_progress 合法（快速流）；todo→in_design 非法；
- 后段共用：in_review→final_review→done；
- blocked 全向可达（含 done→blocked）；previous_status 记录与解除恢复；
- task_status_history：set_status / claim 路径均落历史；status-history 端点可查。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import service
from agentboard.models import Base, TaskStatusHistory
from agentboard import auth


def _make_env(needs_design: bool = True):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as s:
        user = service.register_user(s, username="u1", password="password123")
        proj = service.create_project(s, name="P", key="P")
        service.add_project_member(s, project_id=proj.id, user_id=user.id, role="owner")
        epic = service.create_epic(s, project_id=proj.id, title="E")
        story = service.create_story(s, epic_id=epic.id, title="S", needs_design=needs_design)
        task = service.create_task(s, project_id=proj.id, story_id=story.id, title="T")
        token = auth.make_token(user.id)
    return sessions, user, proj, story, task, token


def test_needs_design_true_requires_design_segment():
    sessions, user, proj, story, task, token = _make_env(needs_design=True)
    with sessions() as s:
        service.set_status(s, task.id, "todo", changed_by=user.id)
        # 设计评审流：todo → in_design 合法
        service.set_status(s, task.id, "in_design", changed_by=user.id)
        assert s.get(service.Task, task.id).status == "in_design"
        # 设计中 → 提交设计评审
        service.set_status(s, task.id, "design_pending_review", changed_by=user.id)
        # 设计评审通过 → 可开发
        service.set_status(s, task.id, "design_review_approved", changed_by=user.id)
        service.set_status(s, task.id, "in_progress", changed_by=user.id)
        assert s.get(service.Task, task.id).status == "in_progress"
        # 后段：in_review → final_review → done
        service.set_status(s, task.id, "in_review", changed_by=user.id)
        service.set_status(s, task.id, "final_review", changed_by=user.id)
        service.set_status(s, task.id, "done", changed_by=user.id)
        assert s.get(service.Task, task.id).status == "done"


def test_needs_design_true_blocks_skip_to_development():
    sessions, user, proj, story, task, token = _make_env(needs_design=True)
    with sessions() as s:
        service.set_status(s, task.id, "todo", changed_by=user.id)
        try:
            service.set_status(s, task.id, "in_progress", changed_by=user.id)  # 须先进设计
            raise AssertionError("expected IllegalTransition for todo->in_progress (needs_design=true)")
        except service.IllegalTransition:
            pass


def test_needs_design_false_fast_flow_and_design_blocked():
    sessions, user, proj, story, task, token = _make_env(needs_design=False)
    with sessions() as s:
        service.set_status(s, task.id, "todo", changed_by=user.id)
        # 快速流：todo → in_progress 合法
        service.set_status(s, task.id, "in_progress", changed_by=user.id)
        assert s.get(service.Task, task.id).status == "in_progress"
        # 快速流禁止进入设计段
        try:
            service.set_status(s, task.id, "in_design", changed_by=user.id)
            raise AssertionError("expected IllegalTransition for in_progress->in_design (fast flow)")
        except service.IllegalTransition:
            pass
        # 后段同样支持 final_review
        service.set_status(s, task.id, "in_review", changed_by=user.id)
        service.set_status(s, task.id, "final_review", changed_by=user.id)
        assert s.get(service.Task, task.id).status == "final_review"


def test_blocked_omnidirectional_and_previous_status_restore():
    sessions, user, proj, story, task, token = _make_env(needs_design=True)
    with sessions() as s:
        service.set_status(s, task.id, "todo", changed_by=user.id)
        service.set_status(s, task.id, "in_design", changed_by=user.id)
        # 任意状态 → blocked（含设计中）
        service.set_status(s, task.id, "blocked", changed_by=user.id)
        t = s.get(service.Task, task.id)
        assert t.status == "blocked"
        assert t.previous_status == "in_design"  # 记住上一个状态
        # 解除 blocked → 恢复到 previous_status
        service.set_status(s, task.id, "in_design", changed_by=user.id)
        t = s.get(service.Task, task.id)
        assert t.status == "in_design"
        assert t.previous_status is None
        # done → blocked 全向可达
        service.set_status(s, task.id, "design_pending_review", changed_by=user.id)
        service.set_status(s, task.id, "design_review_approved", changed_by=user.id)
        service.set_status(s, task.id, "in_progress", changed_by=user.id)
        service.set_status(s, task.id, "in_review", changed_by=user.id)
        service.set_status(s, task.id, "final_review", changed_by=user.id)
        service.set_status(s, task.id, "done", changed_by=user.id)
        service.set_status(s, task.id, "blocked", changed_by=user.id)
        t = s.get(service.Task, task.id)
        assert t.status == "blocked" and t.previous_status == "done"


def test_status_history_recorded_and_queryable():
    sessions, user, proj, story, task, token = _make_env(needs_design=True)
    with sessions() as s:
        service.set_status(s, task.id, "todo", changed_by=user.id)
        service.set_status(s, task.id, "in_design", changed_by=user.id)
        service.set_status(s, task.id, "blocked", changed_by=user.id)
        rows = s.query(TaskStatusHistory).filter(TaskStatusHistory.task_id == task.id).all()
        assert len(rows) == 3
        assert (rows[0].from_status, rows[0].to_status) == ("backlog", "todo")
        assert rows[2].to_status == "blocked"
        assert rows[2].changed_by == user.id
        # service 层查询路径（对应 GET /api/tasks/{tid}/status-history）
        listed = service.list_task_status_history(s, task.id)
        assert len(listed) == 3
        assert listed[0].to_status == "blocked"


def test_claim_writes_status_history():
    sessions, user, proj, story, task, token = _make_env(needs_design=False)
    with sessions() as s:
        service.claim_development_task(s, task.id, user_id=user.id)
        rows = s.query(TaskStatusHistory).filter(TaskStatusHistory.task_id == task.id).all()
        assert len(rows) == 1
        assert (rows[0].from_status, rows[0].to_status) == ("backlog", "in_progress")
        assert rows[0].reason == "claim"
