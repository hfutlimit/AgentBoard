"""Projects service:Project / Epic / Story / Sprint / ProjectMember / ReviewVote。

Phase 4 第三段:从 service.py 拆出。复杂逻辑(状态机/多步骤)留 service.py 后续批次。

老 import 路径兼容:service.py 末尾重绑所有函数到本模块。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容
from ...core.common.enums import ItemType, SprintStatus, Status
from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
)
from ...core.service_helpers import (
    _commit, _invalidate_project_stats_cache, _paginate, _required,
)
from .models import (
    Epic, Project, ProjectMember, ReviewVote, Sprint, Story, StoryStatusHistory,
)
from ..identity.models import User
from ..work_items.models import Task

log = logging.getLogger("agentboard.features.projects.service")

def get_story_project_id(s: Session, story_id: int) -> int | None:
    st = s.get(Story, story_id)
    if not st:
        return None
    e = s.get(Epic, st.epic_id)
    return e.project_id if e else None



def list_sprints(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Sprint).filter(Sprint.project_id == project_id)
    return _paginate(q, limit, offset).all()



def create_epic(s: Session, *, project_id: int, title: str, description: str = "") -> Epic:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    ep = Epic(project_id=project_id, title=_required(title, "title", 300), description=description or "")
    s.add(ep); _commit(s); s.refresh(ep)
    # 2026-08-09：创建 Epic 默认自动创建 1 个默认 Story（标题/描述继承 Epic），
    # Story 创建会自动带 design + 开发 Task，即「创建 Epic 默认创建 Story/Task」。
    create_story(s, epic_id=ep.id, title=ep.title, description=ep.description)
    return ep



def update_sprint(s: Session, id: int, **fields) -> Sprint | None:
    sp = s.get(Sprint, id)
    if not sp:
        return None
    for k, v in fields.items():
        if k in ("title", "goal") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            setattr(sp, k, v)
        elif k == "start_date" and v is not None:
            sp.start_date = v
        elif k == "end_date" and v is not None:
            sp.end_date = v
    _commit(s); s.refresh(sp); return sp



def get_project_stats(s: Session, project_id: int) -> dict:
    """返回项目统计：每日新增/开发/完成任务量（最近 30 天）

    优化：使用单个查询获取多个统计值，减少数据库往返次数
    """
    from datetime import timedelta, datetime as dt
    from sqlalchemy import func, case
    now = dt.now()
    thirty_days_ago = now - timedelta(days=30)

    # 使用条件聚合一次获取所有计数统计
    stats = (
        s.query(
            func.count(Task.id).label("total"),
            func.sum(case((Task.status == Status.DONE, 1), else_=0)).label("done"),
            func.sum(case((Task.status == "backlog", 1), else_=0)).label("backlog"),
            func.sum(case(
                (Task.status.in_(["in_progress", "in_review", "verifying"]), 1),
                else_=0
            )).label("active"),
        )
        .filter(Task.project_id == project_id)
        .first()
    )
    total_tasks = stats.total or 0
    done_tasks = stats.done or 0
    backlog_tasks = stats.backlog or 0
    active_tasks = stats.active or 0

    # 每日新建任务数
    daily_created = (
        s.query(
            func.date(Task.created_at).label("day"),
            func.count(Task.id).label("count"),
        )
        .filter(Task.project_id == project_id, Task.created_at >= thirty_days_ago)
        .group_by(func.date(Task.created_at))
        .order_by(func.date(Task.created_at))
        .all()
    )

    # 每日完成任务数（status 变为 done）
    daily_done = (
        s.query(
            func.date(Task.updated_at).label("day"),
            func.count(Task.id).label("count"),
        )
        .filter(
            Task.project_id == project_id,
            Task.status == Status.DONE,
            Task.updated_at >= thirty_days_ago,
        )
        .group_by(func.date(Task.updated_at))
        .order_by(func.date(Task.updated_at))
        .all()
    )

    return {
        "daily_created": [{"day": str(r.day), "count": r.count} for r in daily_created],
        "daily_done": [{"day": str(r.day), "count": r.count} for r in daily_done],
        "active_tasks": active_tasks,
        "backlog_tasks": backlog_tasks,
        "total_tasks": total_tasks,
        "done_tasks": done_tasks,
        "completion_rate": round(done_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0,
    }


# ---------- Admin: user management ----------

def complete_sprint(s: Session, id: int) -> Sprint:
    """完成 Sprint：将其状态改为 completed，未完成任务退回 todo。

    Story 265 修复：直接 SQL UPDATE 必须同时清 status_reason / previous_status，
    否则 blocked 任务退回后会残留「status=todo + status_reason=blocked_reason」
    的不一致状态，违反「非 done/blocked 必清 reason」业务规则。
    """
    sp = s.get(Sprint, id)
    if not sp:
        raise NotFound(f"sprint {id} not found")
    if sp.status == SprintStatus.COMPLETED:
        raise InvalidValue("sprint is already completed")
    sp.status = SprintStatus.COMPLETED
    # 未完成任务退回 todo（backlog 已下线），并清掉残留的 reason / previous_status
    s.query(Task).filter(
        Task.sprint_id == sp.id,
        Task.status.notin_([Status.DONE])
    ).update({
        "sprint_id": None,
        "status": Status.TODO,
        "status_reason": None,
        "previous_status": None,
    })
    _commit(s); s.refresh(sp); return sp



def remove_project_member(s: Session, project_id: int, user_id: int) -> bool:
    pm = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if not pm:
        return False
    if pm.role == "owner":
        # 检查是否还有其他人是 owner
        owner_count = (
            s.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id, ProjectMember.role == "owner")
            .count()
        )
        if owner_count <= 1:
            raise InvalidValue("cannot remove the last owner from a project")
    s.delete(pm); _commit(s); return True



def list_projects(s: Session, limit: int | None = None, offset: int = 0):
    q = s.query(Project).order_by(Project.id.desc())
    return _paginate(q, limit, offset).all()



def get_sprint_burndown(s: Session, sprint_id: int) -> dict:
    """返回 Sprint 燃尽图数据：每日剩余任务数。"""
    from datetime import timedelta, datetime as dt
    from sqlalchemy import func

    sp = s.get(Sprint, sprint_id)
    if not sp:
        raise NotFound(f"sprint {sprint_id} not found")

    # 统计总任务数
    total = s.query(func.count(Task.id)).filter(Task.sprint_id == sprint_id).scalar() or 0

    # 已完成任务数
    done = s.query(func.count(Task.id)).filter(
        Task.sprint_id == sprint_id, Task.status == Status.DONE
    ).scalar() or 0

    # 理想燃尽：从 start_date 每天递减，到 end_date 为 0
    # 如果没有 start_date，从今天往前推 14 天
    today = dt.now().date()
    if sp.start_date:
        start = sp.start_date.date() if hasattr(sp.start_date, 'date') else sp.start_date
    else:
        start = today - timedelta(days=13)
    if sp.end_date:
        end = sp.end_date.date() if hasattr(sp.end_date, 'date') else sp.end_date
    else:
        end = today

    # 生成每日剩余任务数（理想线 = 线性递减）
    days = []
    ideal = []
    total_days = max((end - start).days, 1)
    for i in range(total_days + 1):
        day = start + timedelta(days=i)
        # 剩余 = 总任务 - (i/total_days * 总任务) = 总任务 * (1 - i/total_days)
        ideal_val = round(total * (1 - i / total_days)) if total_days > 0 else 0
        # 实际剩余：统计当天及之前完成的任务
        done_by_day = s.query(func.count(Task.id)).filter(
            Task.sprint_id == sprint_id,
            Task.status == Status.DONE,
            func.date(Task.updated_at) <= day,
        ).scalar() or 0
        remaining = total - done_by_day
        days.append({"day": day.isoformat(), "remaining": remaining, "ideal": ideal_val})

    return {
        "sprint_id": sprint_id,
        "title": sp.title,
        "total_tasks": total,
        "done_tasks": done,
        "remaining_tasks": total - done,
        "start_date": sp.start_date.isoformat() if sp.start_date else start.isoformat(),
        "end_date": sp.end_date.isoformat() if sp.end_date else end.isoformat(),
        "status": sp.status.value if hasattr(sp.status, 'value') else sp.status,
        "daily": days,
    }



def get_epic(s: Session, id: int) -> Epic | None:
    return s.get(Epic, id)



def delete_sprint(s: Session, id: int) -> bool:
    sp = s.get(Sprint, id)
    if not sp:
        return False
    if sp.status == SprintStatus.ACTIVE:
        raise InvalidValue("cannot delete an active sprint")
    # 将关联任务解除绑定
    s.query(Task).filter(Task.sprint_id == sp.id).update({"sprint_id": None})
    s.delete(sp); _commit(s); return True



def list_project_members(s: Session, project_id: int, limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = s.query(ProjectMember).filter(ProjectMember.project_id == project_id)
    total = q.count()
    return _paginate(q.order_by(ProjectMember.joined_at.desc()), limit, offset).all(), total



def update_epic(s: Session, id: int, **fields) -> Epic | None:
    ep = s.get(Epic, id)
    if not ep:
        return None
    for k, v in fields.items():
        if k in ("title", "description", "status") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            elif k == "status":
                _check_status(v)
            setattr(ep, k, v)
    _commit(s); s.refresh(ep); return ep



def get_sprint_project_id(s: Session, sprint_id: int) -> int | None:
    sp = s.get(Sprint, sprint_id)
    return sp.project_id if sp else None



def delete_project(s: Session, id: int) -> bool:
    p = s.get(Project, id)
    if not p:
        return False

    document_ids = [x[0] for x in s.query(Document.id).filter(Document.project_id == id).all()]
    if document_ids:
        s.query(DocumentComment).filter(
            DocumentComment.document_id.in_(document_ids)
        ).delete(synchronize_session=False)
        s.query(Document).filter(Document.id.in_(document_ids)).delete(synchronize_session=False)

    proposal_ids = [x[0] for x in s.query(Proposal.id).filter(Proposal.project_id == id).all()]
    if proposal_ids:
        s.query(ProposalQuestion).filter(
            ProposalQuestion.proposal_id.in_(proposal_ids)
        ).delete(synchronize_session=False)
        s.query(ProposalRound).filter(
            ProposalRound.proposal_id.in_(proposal_ids)
        ).delete(synchronize_session=False)
        s.query(Proposal).filter(Proposal.id.in_(proposal_ids)).delete(synchronize_session=False)

    schedule_ids = [
        x[0] for x in s.query(AgentSchedule.id).filter(AgentSchedule.project_id == id).all()
    ]
    if schedule_ids:
        s.query(AgentRun).filter(AgentRun.schedule_id.in_(schedule_ids)).delete(
            synchronize_session=False
        )
        s.query(AgentSchedule).filter(AgentSchedule.id.in_(schedule_ids)).delete(
            synchronize_session=False
        )

    epic_ids = [x[0] for x in s.query(Epic.id).filter(Epic.project_id == id).all()]
    story_ids = []
    if epic_ids:
        story_ids = [x[0] for x in s.query(Story.id).filter(Story.epic_id.in_(epic_ids)).all()]
    task_filter = Task.project_id == id
    if story_ids:
        task_filter = or_(task_filter, Task.story_id.in_(story_ids))
    task_ids = [x[0] for x in s.query(Task.id).filter(task_filter).all()]
    if task_ids:
        s.query(AgentRun).filter(AgentRun.task_id.in_(task_ids)).update(
            {AgentRun.task_id: None}, synchronize_session=False,
        )
        s.query(Task).filter(Task.source_spec_id.in_(task_ids)).update(
            {Task.source_spec_id: None}, synchronize_session=False,
        )
        s.query(TaskDependency).filter(or_(
            TaskDependency.task_id.in_(task_ids),
            TaskDependency.depends_on_id.in_(task_ids),
        )).delete(synchronize_session=False)
        s.query(Attachment).filter(Attachment.task_id.in_(task_ids)).delete(
            synchronize_session=False
        )
        s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
        s.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session=False)
    if story_ids:
        s.query(Story).filter(Story.id.in_(story_ids)).delete(synchronize_session=False)
    s.query(Epic).filter(Epic.project_id == id).delete(synchronize_session=False)
    s.query(Sprint).filter(Sprint.project_id == id).delete(synchronize_session=False)
    s.query(ProjectMember).filter(ProjectMember.project_id == id).delete(synchronize_session=False)
    s.query(WebhookConfig).filter(WebhookConfig.project_id == id).delete(synchronize_session=False)
    s.delete(p); _commit(s); return True


# ---------- Epic ----------

def list_epics(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Epic).filter(Epic.project_id == project_id)
    return _paginate(q, limit, offset).all()



def get_project(s: Session, id: int) -> Project | None:
    return s.get(Project, id)



def update_story(s: Session, id: int, **fields) -> Story | None:
    st = s.get(Story, id)
    if not st:
        return None
    status_changed: str | None = None
    for k, v in fields.items():
        if k in ("title", "description", "status", "needs_design", "in_kanban") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            elif k == "status":
                # Story 强制迁移（Ticket 全流程）：单步查表 + blocked 全向特判；
                # 校验失败抛 IllegalTransition（HTTP 400）。
                if v not in STORY_STATUSES:
                    raise InvalidValue(f"invalid status '{v}'")
                new = str(v)
                old = st.status
                if old != new and new != "blocked" and new not in STORY_TRANSITIONS.get(old, set()):
                    raise IllegalTransition(f"{old} -> {new} 不合法")
                if old != new:
                    status_changed = old
            setattr(st, k, v)
    if status_changed is not None:
        # 所有状态写路径统一记历史（Ticket 全流程）：PATCH status 亦记录
        _record_story_status_history(s, id, status_changed, st.status, reason="更新")
    _commit(s); s.refresh(st)
    return st



def create_story(s: Session, *, epic_id: int, title: str, description: str = "",
                 needs_design: bool = True) -> Story:
    """创建 Story，并自动创建 2 个默认 Task（2026-08-09 文档 #60）：

    - design task（type=design）「设计：<标题>」：每个 Story 必需的设计任务，
      承载设计评审（in_design → design_pending_review → design_review_approved 流）；
    - 开发 task（type=task）「实现：<标题>」。

    Story 与默认 Task 同一事务提交；标题截断 300 字符。
    """
    epic = s.get(Epic, epic_id)
    if not epic:
        raise NotFound(f"epic {epic_id} not found")
    st = Story(epic_id=epic_id, title=_required(title, "title", 300),
               description=description or "", needs_design=needs_design)
    s.add(st)
    s.flush()  # 取 st.id 供默认 Task 关联
    base = st.title.strip()
    s.add_all([
        Task(project_id=epic.project_id, story_id=st.id, type=ItemType.DESIGN,
             title=f"设计：{base}"[:300]),
        Task(project_id=epic.project_id, story_id=st.id, type=ItemType.DEV,
             title=f"实现：{base}"[:300]),
    ])
    _invalidate_project_stats_cache(epic.project_id)
    _commit(s); s.refresh(st); return st



def create_sprint(s: Session, *, project_id: int, title: str,
                  goal: str = "", start_date=None, end_date=None) -> Sprint:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    sp = Sprint(project_id=project_id,
                title=_required(title, "title", 300),
                goal=goal or "",
                start_date=start_date, end_date=end_date)
    s.add(sp); _commit(s); s.refresh(sp); return sp



def update_project_member_role(s: Session, project_id: int, user_id: int, role: str) -> ProjectMember | None:
    pm = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if not pm:
        return None
    if role not in ("owner", "member"):
        raise InvalidValue("role must be 'owner' or 'member'")
    pm.role = role; _commit(s); s.refresh(pm); return pm



def delete_epic(s: Session, id: int) -> bool:
    ep = s.get(Epic, id)
    if not ep:
        return False
    for st in s.query(Story).filter(Story.epic_id == id):
        task_ids = [x[0] for x in s.query(Task.id).filter(Task.story_id == st.id).all()]
        if task_ids:
            s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
        s.query(Task).filter(Task.story_id == st.id).delete()
    s.query(Comment).filter(Comment.story_id.in_(
        s.query(Story.id).filter(Story.epic_id == id)
    )).delete(synchronize_session=False)
    s.query(Comment).filter(Comment.epic_id == id).delete(synchronize_session=False)
    s.query(Story).filter(Story.epic_id == id).delete()
    s.delete(ep); _commit(s); return True


# ---------- Story ----------

def get_project_member(s: Session, project_id: int, user_id: int) -> ProjectMember | None:
    return (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )


# ---------- Child-resource -> project resolution (access control) ----------

def activate_sprint(s: Session, id: int) -> Sprint:
    """激活 Sprint：先停用同项目所有 ACTIVE Sprint，再激活目标 Sprint。"""
    sp = s.get(Sprint, id)
    if not sp:
        raise NotFound(f"sprint {id} not found")
    if sp.status == SprintStatus.COMPLETED:
        raise InvalidValue("cannot activate a completed sprint")
    # 停用同项目所有 ACTIVE Sprint
    s.query(Sprint).filter(
        Sprint.project_id == sp.project_id,
        Sprint.status == SprintStatus.ACTIVE,
        Sprint.id != sp.id
    ).update({"status": SprintStatus.PLANNING})
    sp.status = SprintStatus.ACTIVE
    _commit(s); s.refresh(sp); return sp



def get_story(s: Session, id: int) -> Story | None:
    return s.get(Story, id)



def add_project_member(
    s: Session, *, project_id: int, user_id: int, role: str = "member",
) -> ProjectMember:
    """将用户加入项目（自动分配 owner 为创建者，或由管理员添加）"""
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    if not s.get(models.User, user_id):
        raise NotFound(f"user {user_id} not found")
    existing = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if existing:
        raise Duplicate(f"user {user_id} already in project {project_id}")
    if role not in ("owner", "member"):
        raise InvalidValue("role must be 'owner' or 'member'")
    pm = ProjectMember(project_id=project_id, user_id=user_id, role=role)
    s.add(pm); _commit(s); s.refresh(pm); return pm



def update_project(s: Session, id: int, **fields) -> Project | None:
    p = s.get(Project, id)
    if not p:
        return None
    for k, v in fields.items():
        if k == "is_private" and v is not None:
            p.is_private = bool(v)
        elif k in ("name", "key", "description") and v is not None:
            if k == "name":
                v = _required(v, "name", 200)
            elif k == "key":
                v = v.strip() or None
                if v and len(v) > 20:
                    raise InvalidValue("key must be at most 20 characters")
            setattr(p, k, v)
    _commit(s, duplicate=f"project key '{p.key}' already exists" if p.key else None)
    s.refresh(p)
    return p



def get_sprint(s: Session, id: int) -> Sprint | None:
    return s.get(Sprint, id)



def create_project(s: Session, *, name: str, key=None, description: str = "", is_private: bool | None = None) -> Project:
    name = _required(name, "name", 200)
    key = (key or "").strip() or None
    if key and len(key) > 20:
        raise InvalidValue("key must be at most 20 characters")
    p = Project(name=name, key=key, description=description or "")
    # 2026-07-21: 所有项目默认为邀请制（is_private=True）
    p.is_private = True
    s.add(p)
    _commit(s, duplicate=f"project key '{key}' already exists" if key else None)
    s.refresh(p)
    return p



def delete_story(s: Session, id: int) -> bool:
    st = s.get(Story, id)
    if not st:
        return False
    task_ids = [x[0] for x in s.query(Task.id).filter(Task.story_id == id).all()]
    if task_ids:
        s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
    s.query(Comment).filter(Comment.story_id == id).delete(synchronize_session=False)
    s.query(Task).filter(Task.story_id == id).delete()
    s.delete(st); _commit(s); return True


# ---------- Agent 注册表（Epic 122 S1） ----------

def get_epic_project_id(s: Session, epic_id: int) -> int | None:
    e = s.get(Epic, epic_id)
    return e.project_id if e else None



def list_stories(s: Session, epic_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Story).filter(Story.epic_id == epic_id)
    return _paginate(q, limit, offset).all()


