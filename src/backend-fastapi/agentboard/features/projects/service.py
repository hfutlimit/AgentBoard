"""Projects service:Project / Epic / Story / Sprint / ProjectMember / ReviewVote。

Phase 4 第三段:从 service.py 拆出。复杂逻辑(状态机/多步骤)留 service.py 后续批次。

老 import 路径兼容:service.py 末尾重绑所有函数到本模块。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容
from ...core.common.enums import (
    ALL_RUN_STATUSES, ALL_STATUSES, ItemType, SprintStatus, Status,
)
from ...core.common.models import utc_now
from ..identity.models import User
from ..work_items.models import Task

log = logging.getLogger("agentboard.features.projects.service")

from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
    Duplicate,
    IllegalTransition,
)

from ...core.service_helpers import (
    _check_status, _commit, _invalidate_project_stats_cache, _paginate, _required,
    _ser,
)

from .models import (
    Agent, AgentInstance, Epic, Project, ProjectMember,
    ReviewVote, Sprint, Story, StoryStatusHistory, Worker,
    STORY_STATUSES,
    STORY_TRANSITIONS,
)


AGENT_HEARTBEAT_TIMEOUT_SECONDS = 300
# Worker 进程崩溃 / 关机时不会主动调 deregister。read path 在返回 Worker
# 列表前先 reconcile last_heartbeat 超时的行（与 Agent/AgentInstance 同模式）。
# 默认 5 min 与 agent 对齐 —— 已有 ``heartbeat.probe_cli`` 8s 超时 + 30s
# 调度间隔，5 min 留 10× buffer，crash 后 5 min 内被识别。
WORKER_HEARTBEAT_TIMEOUT_SECONDS = 300


def expire_stale_worker_heartbeats(
    s: Session, *, now: datetime | None = None,
    timeout_seconds: int = WORKER_HEARTBEAT_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Atomically mark stale Worker heartbeats as ``inactive``.

    Worker 进程崩溃 / OOM / 主动关机时不会调 deregister。``Worker.status``
    是 routing 决策的输入（PR-1 review：FastAPI 需要知道 ``agent_id`` 实际
    活在哪个 ``worker_id`` 上），stale 行必须被自动降级。

    条件 UPDATE 谓词保护并发 fresh heartbeat 不被 stale reader 覆盖
    （与 ``expire_stale_agent_heartbeats`` 同模式）。
    """
    if timeout_seconds <= 0:
        raise InvalidValue("timeout_seconds must be positive")
    cutoff = (now or utc_now()) - timedelta(seconds=timeout_seconds)
    result = s.execute(
        update(Worker).where(
            Worker.status == "active",
            or_(
                Worker.last_heartbeat.is_(None),
                Worker.last_heartbeat < cutoff,
            ),
        ).values(status="inactive")
    )
    workers_offline = max(result.rowcount or 0, 0)
    if workers_offline:
        _commit(s)
        log.warning(
            "expire_stale_worker_heartbeats: %d 个 worker 因心跳超时被置 inactive",
            workers_offline,
        )
    return {"workers_offline": workers_offline}


def expire_stale_agent_heartbeats(
    s: Session, *, now: datetime | None = None,
    timeout_seconds: int = AGENT_HEARTBEAT_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Atomically mark stale Agent/AgentInstance heartbeats offline.

    ``Agent.online`` is a persisted cache used by list filters and reviewer
    assignment. A crashed Worker cannot clear that cache, so every read path
    that relies on it first reconciles rows older than the five-minute lease.
    The conditional UPDATE predicates also protect a concurrent fresh
    heartbeat from being overwritten by a stale reader.

    A logical Agent remains online when either its legacy direct heartbeat is
    fresh or at least one enabled AgentInstance has a fresh online heartbeat.
    """
    if timeout_seconds <= 0:
        raise InvalidValue("timeout_seconds must be positive")
    cutoff = (now or utc_now()) - timedelta(seconds=timeout_seconds)

    instance_result = s.execute(
        update(AgentInstance).where(
            AgentInstance.online.is_(True),
            or_(
                AgentInstance.last_heartbeat.is_(None),
                AgentInstance.last_heartbeat < cutoff,
            ),
        ).values(online=False)
    )

    fresh_instance_exists = s.query(AgentInstance.id).filter(
        AgentInstance.agent_id == Agent.agent_id,
        AgentInstance.enabled.is_(True),
        AgentInstance.online.is_(True),
        AgentInstance.last_heartbeat.is_not(None),
        AgentInstance.last_heartbeat >= cutoff,
    ).exists()
    agent_result = s.execute(
        update(Agent).where(
            Agent.online.is_(True),
            or_(Agent.last_heartbeat.is_(None), Agent.last_heartbeat < cutoff),
            ~fresh_instance_exists,
        ).values(online=False)
    )

    instances_offline = max(instance_result.rowcount or 0, 0)
    agents_offline = max(agent_result.rowcount or 0, 0)
    if instances_offline or agents_offline:
        _commit(s)
    return {
        "instances_offline": instances_offline,
        "agents_offline": agents_offline,
    }

from ..documents.models import (
    Document,
    DocumentComment,
)

from ..proposals.models import (
    Proposal,
    ProposalQuestion,
    ProposalRound,
    ProposalTicketRequest,
)

from ..scheduling.models import (
    AgentRun,
    AgentSchedule,
)

from ..work_items.models import (
    Attachment,
    Comment,
    TaskDependency,
    WebhookConfig,
)


def get_story_project_id(s: Session, story_id: int) -> int | None:
    st = s.get(Story, story_id)
    if not st:
        return None
    e = s.get(Epic, st.epic_id)
    return e.project_id if e else None


def list_sprints(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Sprint).filter(Sprint.project_id == project_id)
    return _paginate(q, limit, offset).all()


def create_epic(s: Session, *, project_id: int, title: str, description: str = "",
               commit: bool = True) -> Epic:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    ep = Epic(project_id=project_id, title=_required(title, "title", 300), description=description or "")
    s.add(ep)
    s.flush()  # 取 ep.id
    # 2026-08-09：创建 Epic 默认自动创建 1 个默认 Story（标题/描述继承 Epic），
    # Story 创建会自动带 design + 开发 Task，即「创建 Epic 默认创建 Story/Task」。
    # Review 2026-08-26 P1 #2：commit 透传给 Story，让 transaction 边界由 caller 控
    create_story(s, epic_id=ep.id, title=ep.title, description=ep.description, commit=commit)
    if commit:
        s.refresh(ep)
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
    # 8/17 review P1：Task.status 已收敛为 5 态（todo/in_progress/in_review/
    # done/blocked），旧 "backlog" / "verifying" 永远 0 → UI 静默坏。改成
    # Status.TODO（替代 backlog）和不含 verifying（5 态无 verifying）的 active。
    # 字段名 backlog_tasks 保留以兼容现有 front-end 契约（app.html 第 974 行
    # 仍引用），内部计算切换到 todo。
    stats = (
        s.query(
            func.count(Task.id).label("total"),
            func.sum(case((Task.status == Status.DONE, 1), else_=0)).label("done"),
            func.sum(case((Task.status == Status.TODO, 1), else_=0)).label("backlog"),
            func.sum(case(
                (Task.status.in_([Status.IN_PROGRESS, Status.IN_REVIEW]), 1),
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


def remove_project_member(
    s: Session, project_id: int, user_id: int,
) -> dict:
    """移除项目成员（T2.2）：不能删最后一个 owner；其 owned item 随移除移交。

    移交规则（Plan T2.2，接收方按 T2.0 规则解析）：
    - 接收方 = 除被移除者之外、joined_at 最早的 owner（``resolve_project_owner_excluding``）；
    - 接收方必须存在 —— **不存在则拒绝移除**，而不是删完留一堆 owner 指向
      非成员的孤儿 task（违反 T1.4 起维护的「owner ∈ ProjectMember」不变量，
      且执行门对这种情况不会拦：它只判 owner 相等，不判成员关系）。
      报错信息直接告诉管理员先补一个 owner；
    - 在途 run 不中断，移交只影响后续步骤（同 T2.3）。

    返回 ``{"removed": True, "transferred_tasks": n, "transferred_stories": m,
    "receiver": uid}``，数量供 T5.1/T5.2（通知/历史）复用。
    """
    pm = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if not pm:
        raise NotFound(f"user {user_id} is not a member of project {project_id}")

    if pm.role == "owner":
        # 检查是否还有其他人是 owner（最后 owner 不可删，保留原护栏）
        owner_count = (
            s.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id,
                    ProjectMember.role == "owner")
            .count()
        )
        if owner_count <= 1:
            raise InvalidValue("cannot remove the last owner from a project")

    receiver = resolve_project_owner_excluding(s, project_id, user_id)
    # 只有当被移除者名下**确实有** item 时，接收方缺失才是问题；否则纯移除即可
    owned_tasks = s.query(Task).filter(
        Task.project_id == project_id, Task.owner_user_id == user_id).all()
    owned_stories = s.query(Story).filter(
        Story.owner_user_id == user_id).all()
    owned_stories = [st for st in owned_stories
                     if _story_project_id(s, st.id) == project_id]
    if (owned_tasks or owned_stories) and receiver is None:
        raise InvalidValue(
            f"cannot remove user {user_id}: they own "
            f"{len(owned_tasks)} task(s) / {len(owned_stories)} story(s) in "
            f"project {project_id}, but the project has no other owner to "
            "receive them. Assign an owner first."
        )

    s.delete(pm)
    for t in owned_tasks:
        t.owner_user_id = receiver
    for st in owned_stories:
        st.owner_user_id = receiver
    _commit(s)
    if owned_tasks or owned_stories:
        log.info(
            "remove_project_member: user %s 移出 project %s，其 %s 个 task /"
            " %s 个 story 移交给 project owner %s（在途 run 不中断）",
            user_id, project_id, len(owned_tasks), len(owned_stories), receiver,
        )
    return {
        "removed": True,
        "transferred_tasks": len(owned_tasks),
        "transferred_stories": len(owned_stories),
        "receiver": receiver,
    }


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


def project_owners(s: Session, project_id: int) -> list[ProjectMember]:
    """该项目的所有 owner 成员行，按「入伙时间早 → id 小」排序。

    T2.0 之后 (project_id,user_id) 唯一，**同一个人不可能有两行**，所以返回
    长度 >1 只可能是「多个人都挂着 owner」—— 那是数据异常，不是常态。
    """
    return (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id,
                ProjectMember.role == "owner")
        .order_by(ProjectMember.joined_at.asc(), ProjectMember.id.asc())
        .all()
    )


def resolve_project_owner(s: Session, project_id: int) -> int | None:
    """确定 project 的 owner user_id（T2.0 选取规则）。

    规则：取 ``joined_at`` 最早的 owner 行（并列时取 id 小的）—— 结果确定，
    同样的输入永远得到同样的输出。

    **多个 owner 属数据异常，会打 WARNING 报冲突**。之所以不直接抛异常：
    本函数是 T2.2「移除成员 → 移交 project owner」的接收方解析入口，抛异常
    会让「移除成员」这个本来能成功的操作连带失败，把一处数据脏污放大成功能
    不可用。报出来让人修，比停下来好。

    返回 None = 该项目一个 owner 都没有（T1.4 回填会补 admin 兜底）。
    """
    owners = project_owners(s, project_id)
    if not owners:
        log.warning(
            "resolve_project_owner: project %s 没有 role='owner' 的成员", project_id)
        return None
    if len(owners) > 1:
        log.warning(
            "resolve_project_owner: project %s 有 %s 个 owner（%s），"
            "属数据异常，需人工收敛成一个；本次按 joined_at 最早者 %s 处理",
            project_id, len(owners), [pm.user_id for pm in owners],
            owners[0].user_id,
        )
    return int(owners[0].user_id)


def resolve_project_owner_excluding(
    s: Session, project_id: int, exclude_user_id: int,
) -> int | None:
    """同上，但把 ``exclude_user_id`` 排除在候选之外。

    T2.2 移除成员时用：被移除的人如果自己也是 owner（且不是最后一个），
    他名下的 task/story 要移交给**其余** owner —— 不能移交给他自己，
    否则刚删掉的人马上又变成接收方，移交等于没发生。
    """
    owners = [
        pm for pm in project_owners(s, project_id)
        if pm.user_id != exclude_user_id
    ]
    if not owners:
        return None
    return int(owners[0].user_id)


# ---- T2.3 Story 移交 ----------------------------------------------------------

def transfer_story(
    s: Session, story_id: int, new_owner_user_id: int, *,
    changed_by_user_id: int | None = None,
) -> tuple[Story, int | None]:
    """移交 story 归属（T2.3）：免确认、即生效。

    规则与 ``features/work_items/service.py::transfer_task`` 完全一致
    （新 owner 必须是项目成员；只改 owner_user_id 不动 created_by）。
    Story 没有 assignee / 在途 run 概念，移交即对后续一切生效。
    """
    st = s.get(Story, story_id)
    if not st:
        raise NotFound(f"story {story_id} not found")
    if not user_is_project_member(s, _story_project_id(s, story_id),
                                 new_owner_user_id):
        raise InvalidValue(
            f"new owner user {new_owner_user_id} is not a member of the "
            f"story's project; transfer aborted"
        )
    previous = st.owner_user_id
    st.owner_user_id = new_owner_user_id
    _commit(s)
    log.info(
        "transfer_story: story %s owner %s -> %s (changed_by=%s)",
        story_id, previous, new_owner_user_id, changed_by_user_id,
    )
    return st, previous


def _story_project_id(s: Session, story_id: int) -> int | None:
    epic = s.query(Epic.id, Epic.project_id).join(
        Story, Story.epic_id == Epic.id).filter(Story.id == story_id).first()
    return int(epic[1]) if epic else None


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


def _record_story_status_history(s: Session, story_id: int, from_status: str, to_status: str,
                                 *, changed_by: int | None = None, reason: str = "") -> None:
    """Story 状态变更历史（story_status_history）：全部状态变更路径统一调用。"""
    s.add(StoryStatusHistory(
        story_id=story_id, from_status=from_status, to_status=to_status,
        changed_by=changed_by, reason=reason or "",
    ))


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
                 needs_design: bool = True, commit: bool = True,
                 create_default_tasks: bool = True,
                 design_needs_human_confirmation: bool = True,
                 created_by_user_id: int | None = None) -> Story:
    """创建 Story，并自动创建 2 个默认 Task（2026-08-09 文档 #60）：

    - design task（type=design）「设计：<标题>」：每个 Story 必需的设计任务，
      承载设计评审（in_design → design_pending_review → design_review_approved 流）；
    - 开发 task（type=task）「实现：<标题>」。

    Story 与默认 Task 同一事务提交；标题截断 300 字符。

    Review 2026-08-26 P1 #2：加 ``commit: bool = True`` 参数。
    - commit=True（默认）：维持原行为，Story + 2 Task + 1 Dependency + 缓存失效
      同一 commit。向后兼容所有现有 caller。
    - commit=False：仅 flush 不 commit，让 caller（通常是 ProposalConversionService
      那种"transaction owner"）统一收尾。
      设计原则：``Transaction belongs to use case, not entity helper``。
    """
    epic = s.get(Epic, epic_id)
    if not epic:
        raise NotFound(f"epic {epic_id} not found")
    # T1.5：Story 与默认 Task 都要带上 owner。
    # created_by_user_id 是不可变审计列、owner_user_id 是可变归属列（T2.3 移交
    # 只改后者），两者在创建时同值，之后可能分叉 —— 所以两个列都要写，不能只写一个。
    st = Story(epic_id=epic_id, title=_required(title, "title", 300),
               description=description or "", needs_design=needs_design,
               created_by_user_id=created_by_user_id,
               owner_user_id=created_by_user_id)
    s.add(st)
    s.flush()  # 取 st.id 供默认 Task 关联
    if not create_default_tasks:
        if commit:
            _invalidate_project_stats_cache(epic.project_id)
            _commit(s)
        s.refresh(st)
        return st
    base = st.title.strip()
    design_task = None
    if needs_design:
        design_task = Task(project_id=epic.project_id, story_id=st.id, type=ItemType.DESIGN,
                           title=f"设计：{base}"[:300],
                           needs_human_confirmation=design_needs_human_confirmation,
                           created_by_user_id=created_by_user_id,
                           owner_user_id=created_by_user_id)
        s.add(design_task)
        s.flush()
    dev_task = Task(project_id=epic.project_id, story_id=st.id, type=ItemType.DEV,
                    title=f"实现：{base}"[:300],
                    created_by_user_id=created_by_user_id,
                    owner_user_id=created_by_user_id)
    s.add(dev_task)
    s.flush()
    if design_task is not None:
        s.add(TaskDependency(task_id=dev_task.id, depends_on_id=design_task.id,
                             dependency_type="blocks"))
    if commit:
        _invalidate_project_stats_cache(epic.project_id)
        _commit(s)
    s.refresh(st)
    return st


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


def _delete_task_defensive(s: Session, task_id: int) -> None:
    """删 task + 清理所有指向它的 FK 引用（Epic 140 防御性级联）。

    复刻 ``agentboard.service.delete_task``（中央 facade）的清理策略：避免
    delete_epic / delete_story 绕过 facade 漏清 ``task_outcome`` / ``episode_embedding``
    / ``project_playbook*`` 等 NO ACTION FK → 撞 ``FOREIGN KEY constraint failed``。
    每次 commit 独立，性能可接受（epic/story 删除非热路径）。
    """
    from ...service import delete_task as _facade_delete_task  # type: ignore[attr-defined]
    _facade_delete_task(s, task_id)


def delete_epic(s: Session, id: int) -> bool:
    """删除 Epic（FK 防御性级联，v7.3 e2e 收尾修复）。

    **根因（v7.3 e2e 收尾发现）**：旧实现只清 ``Comment + Task + Story + Epic``，
    绕过了中央 ``delete_task`` 的防御性清理（Epic 140 切片 1/3 引入 ``task_outcome`` /
    ``episode_embedding`` / ``project_playbook*`` 后旧 facade 没跟进）。当 epic 下
    task 走过 done（落 outcome + episode + playbook_episode）后再删 epic，SQLite
    抛 ``FOREIGN KEY constraint failed``，HTTP 500。story 删除同病。

    清理顺序：
    1. 逐 task 调中央 ``delete_task``（自带 learning/dependency/comment 清理）；
    2. 清 story 级 FK（comment + review_votes 终态锚点解绑）；
    3. 解绑 ``agent_schedules.epic_id``（NO ACTION，置 NULL 保留 schedule）；
    4. 删 story、epic。
    """
    ep = s.get(Epic, id)
    if not ep:
        return False
    story_ids = [x[0] for x in s.query(Story.id).filter(Story.epic_id == id).all()]
    # 1) 逐 task 走中央 delete_task
    for sid in story_ids:
        for tid in [x[0] for x in s.query(Task.id).filter(Task.story_id == sid).all()]:
            _delete_task_defensive(s, tid)
    # 2) 清 story 级 FK：先将 review_votes.comment_id 置 NULL 并解绑 entity_id，再删 Comment
    if story_ids:
        # review_votes 没有 story_id FK，只能按 entity_type=story 清；保留投票历史
        # 先 NULL 化 vote.comment_id，防 NO ACTION FK 撞，再解绑 entity_id
        s.query(ReviewVote).filter(
            ReviewVote.entity_type == "story",
            ReviewVote.entity_id.in_(story_ids),
        ).update({ReviewVote.entity_id: -1, ReviewVote.comment_id: None}, synchronize_session=False)
        s.query(Comment).filter(Comment.story_id.in_(story_ids)).delete(synchronize_session=False)
    s.query(Comment).filter(Comment.epic_id == id).delete(synchronize_session=False)
    # 3) 解绑 agent_schedules.epic_id（NO ACTION，置 NULL 保留 schedule）
    s.query(AgentSchedule).filter(AgentSchedule.epic_id == id).update(
        {AgentSchedule.epic_id: None}, synchronize_session=False,
    )
    # 4) 删 story + epic
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
        elif k == "is_archived" and v is not None:
            # Story 137：归档/取消归档。归档时打时间戳/操作人；取消时清空。
            p.is_archived = bool(v)
            if p.is_archived:
                from datetime import datetime
                p.archived_at = datetime.utcnow()
            else:
                p.archived_at = None
                p.archived_by = None
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


# ---- Story 137：项目归档（与 list 配合） ----

def archive_project(s: Session, project_id: int, *, user_id: int | None = None) -> Project | None:
    """归档项目。重复归档幂等。"""
    from datetime import datetime
    p = s.get(Project, project_id)
    if not p:
        return None
    if not p.is_archived:
        p.is_archived = True
        p.archived_at = datetime.utcnow()
        p.archived_by = user_id
        _commit(s)
        s.refresh(p)
    return p


def unarchive_project(s: Session, project_id: int) -> Project | None:
    """恢复归档项目。"""
    p = s.get(Project, project_id)
    if not p:
        return None
    if p.is_archived:
        p.is_archived = False
        p.archived_at = None
        p.archived_by = None
        _commit(s)
        s.refresh(p)
    return p


def bulk_archive(s: Session, project_ids: list[int], *, user_id: int | None = None) -> int:
    """批量归档。返回实际变更数量（已归档的会跳过）。"""
    from datetime import datetime
    if not project_ids:
        return 0
    now = datetime.utcnow()
    affected = (
        s.query(Project)
        .filter(Project.id.in_(project_ids), Project.is_archived.is_(False))
        .update(
            {Project.is_archived: True, Project.archived_at: now, Project.archived_by: user_id},
            synchronize_session=False,
        )
    )
    _commit(s)
    return int(affected or 0)


def bulk_unarchive(s: Session, project_ids: list[int]) -> int:
    """批量恢复归档。"""
    if not project_ids:
        return 0
    affected = (
        s.query(Project)
        .filter(Project.id.in_(project_ids), Project.is_archived.is_(True))
        .update(
            {Project.is_archived: False, Project.archived_at: None, Project.archived_by: None},
            synchronize_session=False,
        )
    )
    _commit(s)
    return int(affected or 0)


# ---- Story 137：项目中心列表（含 task/member 统计 + 最近活跃） ----

def _project_stats_dict(s: Session, project_ids: list[int]) -> dict[int, dict]:
    """批量计算项目统计：task_count / task_done / member_count / last_activity_at。

    返回 {project_id: {task_count, task_done, member_count, last_activity_at}}。
    project_ids 为空时返回空 dict。
    """
    from sqlalchemy import func as sa_func
    from datetime import datetime

    if not project_ids:
        return {}

    # 任务统计（按 status 分组聚合）
    task_rows = (
        s.query(Task.project_id, Task.status, sa_func.count(Task.id))
        .filter(Task.project_id.in_(project_ids))
        .group_by(Task.project_id, Task.status)
        .all()
    )
    task_total: dict[int, int] = {}
    task_done: dict[int, int] = {}
    for pid, status, count in task_rows:
        task_total[pid] = task_total.get(pid, 0) + count
        if status == Status.DONE:
            task_done[pid] = task_done.get(pid, 0) + count

    # 成员数
    member_rows = (
        s.query(ProjectMember.project_id, sa_func.count(ProjectMember.id))
        .filter(ProjectMember.project_id.in_(project_ids))
        .group_by(ProjectMember.project_id)
        .all()
    )
    member_count = {pid: cnt for pid, cnt in member_rows}

    # 最近活跃时间：取 tasks.updated_at、stories.created_at、epics.created_at 的最大值
    task_activity = dict(
        s.query(Task.project_id, sa_func.max(Task.updated_at))
        .filter(Task.project_id.in_(project_ids))
        .group_by(Task.project_id)
        .all()
    )
    epic_activity = dict(
        s.query(Epic.project_id, sa_func.max(Epic.created_at))
        .filter(Epic.project_id.in_(project_ids))
        .group_by(Epic.project_id)
        .all()
    )
    # Story 走 epic_id 间接关联
    story_rows = (
        s.query(Epic.project_id, sa_func.max(Story.created_at))
        .join(Story, Story.epic_id == Epic.id)
        .filter(Epic.project_id.in_(project_ids))
        .group_by(Epic.project_id)
        .all()
    )
    story_activity = {pid: ts for pid, ts in story_rows}

    out: dict[int, dict] = {}
    for pid in project_ids:
        candidates = [
            task_activity.get(pid),
            story_activity.get(pid),
            epic_activity.get(pid),
        ]
        last_activity_at = max((c for c in candidates if c is not None), default=None)
        out[pid] = {
            "task_count": task_total.get(pid, 0),
            "task_done": task_done.get(pid, 0),
            "member_count": member_count.get(pid, 0),
            "last_activity_at": last_activity_at.isoformat() if isinstance(last_activity_at, datetime) else last_activity_at,
        }
    return out


def _enrich_projects(s: Session, projects: list) -> list[dict]:
    """把 Project ORM 列表包装成带统计字段的 dict 列表（项目中心用）。"""
    pids = [p.id for p in projects]
    stats = _project_stats_dict(s, pids)
    out: list[dict] = []
    for p in projects:
        d = _ser(p)
        st = stats.get(p.id, {})
        d["task_count"] = st.get("task_count", 0)
        d["task_done"] = st.get("task_done", 0)
        d["member_count"] = st.get("member_count", 0)
        d["last_activity_at"] = st.get("last_activity_at")
        out.append(d)
    return out


# ---- 同步自 service.py ----
def list_accessible_projects(
    s: Session, user_id: int | None, limit: int | None = None, offset: int = 0,
    *,
    include_archived: bool | None = None,
) -> tuple[list, int]:
    """返回用户可见的项目列表（Story 137 起默认隐藏已归档）。

    访问规则（2026-07-21 邀请制）：
    - 管理员：可见全部项目（``user.is_admin=True``）。
    - 普通用户：仅可见自己是成员的项目（邀请制）。
    - 未登录：空列表。

    ``abk_`` API Key 经 ``_current_user()`` 解析为关联用户的完整身份
    （含 ``is_admin``），因此权限与用户一致 —— 管理员 key 可见全部，
    普通用户 key 仅见成员项目。

    归档过滤（Story 137）：
    - ``include_archived=None``（默认，路由层不传）：隐藏已归档（``is_archived=True``）；
    - ``include_archived=False``：显式隐藏；
    - ``include_archived=True``：含归档。

    根因说明：旧的 ``list_projects`` 内部不带归档过滤，router 文档承诺"默认隐藏
    归档"是空头支票。修复时把过滤下沉到 service，保证 router / MCP 工具 / 后续
    调用方都拿到一致行为，避免文档与实现分裂。
    """
    if user_id is None:
        q = s.query(Project).filter(False)  # 未登录 → 空
        total = 0
        return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total

    user = s.get(User, user_id)
    if user and user.is_admin:
        # 管理员：全量
        q = s.query(Project)
    else:
        # 普通用户：仅成员项目
        member_project_ids = [
            r[0]
            for r in s.query(ProjectMember.project_id)
            .filter(ProjectMember.user_id == user_id)
            .all()
        ]
        if member_project_ids:
            q = s.query(Project).filter(Project.id.in_(member_project_ids))
        else:
            q = s.query(Project).filter(False)  # 无成员项目 → 空
    # Story 137 归档过滤：默认（None/False）隐藏已归档
    if not include_archived:
        q = q.filter(Project.is_archived.is_(False))
    total = q.count()
    return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total


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
    """删除 Story（FK 防御性级联，v7.3 e2e 收尾修复）。

    根因同 ``delete_epic``：task 走过 done 落 outcome + episode + playbook 后再
    删 story 撞 NO ACTION FK。清理策略：
    1. 逐 task 调中央 ``delete_task``；
    2. 清 story 级 FK（comment + review_votes 锚点解绑）；
    3. 删 story。
    """
    st = s.get(Story, id)
    if not st:
        return False
    task_ids = [x[0] for x in s.query(Task.id).filter(Task.story_id == id).all()]
    # 1) 逐 task 走中央 delete_task
    for tid in task_ids:
        _delete_task_defensive(s, tid)
    # 2) 清 story 级 FK：先将 review_votes.comment_id 置 NULL 并解绑 entity_id，再删 Comment
    s.query(ReviewVote).filter(
        ReviewVote.entity_type == "story",
        ReviewVote.entity_id == id,
    ).update({ReviewVote.entity_id: -1, ReviewVote.comment_id: None}, synchronize_session=False)
    s.query(Comment).filter(Comment.story_id == id).delete(synchronize_session=False)
    # 3) 删 story（StoryStatusHistory 由 ondelete CASCADE 自动清）
    s.delete(st); _commit(s); return True


# ---------- Agent 注册表（Epic 122 S1） ----------

def get_epic_project_id(s: Session, epic_id: int) -> int | None:
    e = s.get(Epic, epic_id)
    return e.project_id if e else None


def list_stories(s: Session, epic_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Story).filter(Story.epic_id == epic_id)
    return _paginate(q, limit, offset).all()




# ---- 同步自 service.py ----
def user_is_project_member(s: Session, project_id: int, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
        is not None
    )


# ---- T2.1 读门 ----------------------------------------------------------------
#
# 为什么不直接用 user_is_project_member：读门是**语义**（这个人能不能看见这个
# 项目的内容），成员判定是**实现**（表里有没有那行）。语义层要吸收两件事：
# admin 全通、未登录全拒 —— 把这两条收进谓词，调用方就不用各自写一遍
# `if is_admin or user_is_project_member(...)`，写漏一处就是一次越权泄漏
# （GET /api/tasks 和 /api/stories 不带 project_id 时正是这么漏的）。

def user_can_read_project(
    s: Session, user_id: int | None, project_id: int, *, is_admin: bool = False,
) -> bool:
    """读门：``user_id`` 能否读取 ``project_id`` 下的内容（文档 / task / story）。

    与执行门（``features/work_items/ownership.py``）职责正交：读门答「能不能
    看见」，执行门答「能不能干」。项目成员应当能读到项目里所有内容（共享读
    是本次重构目标之一），但不因此获得执行权。
    """
    if is_admin:
        return True
    if user_id is None:
        return False
    return user_is_project_member(s, project_id, user_id)


def readable_project_ids(
    s: Session, user_id: int | None, *, is_admin: bool = False,
) -> list[int] | None:
    """读门的集合形式：该用户可读的 project id 列表。

    返回 ``None`` 表示**不受限**（admin）—— 用 None 而不是返回全表 id，因为
    调用方的过滤写法是 ``.in_(pids)``，None 意味着「跳过这层过滤」，空列表
    意味着「一行都看不见」。两种语义必须分开，admin 和「无成员项目的新用户」
    不是一回事。
    """
    if is_admin:
        return None
    if user_id is None:
        return []
    return [
        r[0]
        for r in s.query(ProjectMember.project_id)
        .filter(ProjectMember.user_id == user_id)
        .all()
    ]

# ---- 同步自 service.py ----
def user_is_project_owner(s: Session, project_id: int, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return (
        s.query(ProjectMember)
        .filter(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.role == "owner",
        )
        .first()
        is not None
    )

# ---- 同步自 service.py ----
def get_task_project_id(s: Session, task_id: int) -> int | None:
    task = s.get(Task, task_id)
    if not task:
        return None
    # Task.project_id is the canonical ownership boundary.  story_id is
    # nullable and is only a content relationship; using it for access
    # control incorrectly makes standalone tasks unscoped.
    if task.story_id:
        story_project_id = get_story_project_id(s, task.story_id)
        if story_project_id is not None and story_project_id != task.project_id:
            log.error(
                "task %s has project_id=%s but story %s resolves to project_id=%s",
                task.id, task.project_id, task.story_id, story_project_id,
            )
    return task.project_id

# ---- 同步自 service.py ----
def get_schedule_project_id(s: Session, schedule_id: int) -> int | None:
    sch = s.get(AgentSchedule, schedule_id)
    return sch.project_id if sch else None


def get_run_project_id(s: Session, run_id: int) -> int | None:
    run = s.get(AgentRun, run_id)
    if not run:
        return None
    return get_schedule_project_id(s, run.schedule_id)

# ---- 同步自 service.py ----
def get_comment_project_id(s: Session, comment_id: int) -> int | None:
    c = s.get(Comment, comment_id)
    if not c:
        return None
    if c.task_id is not None:
        return get_task_project_id(s, c.task_id)
    if c.story_id is not None:
        return get_story_project_id(s, c.story_id)
    if c.epic_id is not None:
        return get_epic_project_id(s, c.epic_id)
    return None

# ---- 同步自 service.py ----
def get_dependency_project_id(s: Session, dependency_id: int) -> int | None:
    d = s.get(TaskDependency, dependency_id)
    if not d:
        return None
    return get_task_project_id(s, d.task_id)

# ---- 同步自 service.py ----
def get_webhook_project_id(s: Session, webhook_id: int) -> int | None:
    wh = s.get(WebhookConfig, webhook_id)
    return wh.project_id if wh else None

# ---- 同步自 service.py ----
def get_attachment_project_id(s: Session, attachment_id: int) -> int | None:
    a = s.get(Attachment, attachment_id)
    if not a:
        return None
    return get_task_project_id(s, a.task_id)


# ---- Story 137：项目中心列表（带筛选 / 排序 / 统计） ----
# scope 枚举：
#   "all"      - 默认：可见项目全集（含已归档）
#   "active"   - 仅未归档
#   "archived" - 仅已归档
#   "mine"     - 当前用户作为 owner 或 member 的项目
#   "created"  - 当前用户作为 owner 创建的项目
#
# sort 枚举：
#   "recent"   - 默认：last_activity_at DESC，无活动则 created_at DESC
#   "name"     - name ASC
#   "created"  - created_at DESC
#   "tasks"    - task_count DESC（按完成度排序）
#
# 返回 list[dict]（含统计字段）和 total。
_SORT_MAP = {
    "name": lambda c: c.asc(),
    "created": lambda c: c.desc(),
    "tasks": None,  # 需要 Python 端后排序
}


def list_accessible_projects_center(
    s: Session,
    user_id: int | None,
    *,
    scope: str = "active",
    sort: str = "recent",
    include_archived: bool | None = None,
    limit: int | None = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """项目中心专用列表：带筛选/排序/统计字段的 dict 列表。

    参数：
    - scope：见顶部枚举
    - sort：见顶部枚举（recent 默认按 last_activity_at；tasks 走 Python 端）
    - include_archived：仅当 scope='all' 时生效；True=含归档，False=不含，None=全含
    - limit/offset：分页

    权限：与管理中心 ``list_accessible_projects`` 一致。
    """
    from sqlalchemy import or_, func as sa_func

    if user_id is None:
        return [], 0

    # 1) 基础可见集
    user = s.get(User, user_id)
    is_admin = bool(user and user.is_admin)
    if is_admin:
        q = s.query(Project)
    else:
        member_pids = [
            r[0]
            for r in s.query(ProjectMember.project_id)
            .filter(ProjectMember.user_id == user_id)
            .all()
        ]
        if member_pids:
            q = s.query(Project).filter(Project.id.in_(member_pids))
        else:
            q = s.query(Project).filter(False)

    # 2) scope 筛选
    if scope == "active":
        q = q.filter(Project.is_archived.is_(False))
    elif scope == "archived":
        q = q.filter(Project.is_archived.is_(True))
    elif scope == "all":
        if include_archived is False:
            q = q.filter(Project.is_archived.is_(False))
        # include_archived is None/True → 含归档
    elif scope in ("mine", "created"):
        if not is_admin:
            # 普通用户仅看自己成员项目已由基础集保证
            if scope == "created":
                q = q.join(ProjectMember, ProjectMember.project_id == Project.id).filter(
                    ProjectMember.user_id == user_id,
                    ProjectMember.role == "owner",
                )
        else:
            # 管理员：按成员关系筛选
            q = q.join(ProjectMember, ProjectMember.project_id == Project.id).filter(
                ProjectMember.user_id == user_id
            )
            if scope == "created":
                q = q.filter(ProjectMember.role == "owner")
    else:
        raise InvalidValue(f"invalid scope '{scope}'")

    total = q.count()
    rows = q.all()
    enriched = _enrich_projects(s, rows)

    # 3) 排序（recent/tasks 需要 Python 端处理）
    # last_activity_at 是 ISO 8601 字符串，字典序 = 时间序；None 排到末尾。
    if sort == "recent":
        enriched.sort(
            key=lambda d: (d.get("last_activity_at") or "", d.get("id", 0)),
            reverse=True,
        )
    elif sort == "name":
        enriched.sort(key=lambda d: (d.get("name") or "").lower())
    elif sort == "created":
        enriched.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    elif sort == "tasks":
        enriched.sort(key=lambda d: (-(d.get("task_count") or 0), -int(d.get("is_archived", False)), d.get("id", 0)))
    else:
        raise InvalidValue(f"invalid sort '{sort}'")

    # 4) 分页
    if limit is not None:
        enriched = enriched[offset:offset + limit]

    return enriched, total

# ---- 同步自 service.py ----
def list_user_projects(
    s: Session, user_id: int, *, role: str | None = None,
    limit: int | None = None, offset: int = 0,
) -> tuple[list[tuple[Project, str]], int]:
    q = (
        s.query(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .filter(ProjectMember.user_id == user_id)
    )
    if role is not None:
        if role not in {"owner", "member"}:
            raise InvalidValue("role must be 'owner' or 'member'")
        q = q.filter(ProjectMember.role == role)
    total = q.count()
    return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total

# ---- 同步自 service.py ----
def get_overview(s: Session, user_id: int | None) -> dict:
    """跨项目聚合统计：首页 Dashboard 单请求数据源。

    可见性规则与 ``list_accessible_projects`` 一致：
    - 管理员：全部项目；
    - 普通用户：仅成员项目；
    - 未登录（user_id=None）：空。

    返回结构：
    {
      "counts": {"projects": N, "epics": N, "stories": N, "tasks": N, "done_tasks": N},
      "projects": [{"id", "name", "total", "done", "percent"}],   # 按 total 降序
      "status_distribution": [{"status", "count"}],                # 仅 count>0，按 ALL_STATUSES 顺序
      "activity_7d": [{"day", "count"}],                           # 近 7 天（含 0），按日升序
    }
    """
    from datetime import timedelta, datetime as dt
    from sqlalchemy import case

    projects, _ = list_accessible_projects(s, user_id)
    project_ids = [p.id for p in projects]
    if not project_ids:
        return {
            "counts": {"projects": 0, "epics": 0, "stories": 0, "tasks": 0, "done_tasks": 0},
            "projects": [],
            "status_distribution": [],
            "activity_7d": [],
        }

    epic_count = (
        s.query(func.count(Epic.id)).filter(Epic.project_id.in_(project_ids)).scalar() or 0
    )
    story_count = (
        s.query(func.count(Story.id))
        .join(Epic, Story.epic_id == Epic.id)
        .filter(Epic.project_id.in_(project_ids))
        .scalar() or 0
    )
    task_count = (
        s.query(func.count(Task.id)).filter(Task.project_id.in_(project_ids)).scalar() or 0
    )
    done_tasks = (
        s.query(func.count(Task.id))
        .filter(Task.project_id.in_(project_ids), Task.status == Status.DONE)
        .scalar() or 0
    )

    # 各项目任务进度（含 0 任务项目，按 total 降序）
    per_project = dict(
        s.query(Task.project_id, func.count(Task.id))
        .filter(Task.project_id.in_(project_ids))
        .group_by(Task.project_id)
        .all()
    )
    per_project_done = dict(
        s.query(Task.project_id, func.count(Task.id))
        .filter(Task.project_id.in_(project_ids), Task.status == Status.DONE)
        .group_by(Task.project_id)
        .all()
    )
    projects_out = []
    for p in projects:
        total = per_project.get(p.id, 0)
        done = per_project_done.get(p.id, 0)
        projects_out.append({
            "id": p.id,
            "name": p.name,
            "total": total,
            "done": done,
            "percent": round(done / total * 100) if total else 0,
        })
    projects_out.sort(key=lambda row: (-row["total"], row["id"]))

    # 状态分布（按 ALL_STATUSES 顺序，含 0）
    status_counts = dict(
        s.query(Task.status, func.count(Task.id))
        .filter(Task.project_id.in_(project_ids))
        .group_by(Task.status)
        .all()
    )
    status_distribution = [
        {"status": st, "count": status_counts.get(st, 0)} for st in ALL_STATUSES
    ]

    # 近 7 日活动（按 updated_at 日计数，含 0）
    now = dt.now()
    seven_days_ago = now - timedelta(days=6)
    day_counts = {
        str(day): count
        for day, count in (
            s.query(func.date(Task.updated_at).label("day"), func.count(Task.id))
            .filter(
                Task.project_id.in_(project_ids),
                Task.updated_at >= seven_days_ago,
            )
            .group_by(func.date(Task.updated_at))
            .all()
        )
    }
    activity_7d = [
        {"day": (seven_days_ago + timedelta(days=i)).date().isoformat(),
         "count": day_counts.get((seven_days_ago + timedelta(days=i)).date().isoformat(), 0)}
        for i in range(7)
    ]

    return {
        "counts": {
            "projects": len(project_ids),
            "epics": epic_count,
            "stories": story_count,
            "tasks": task_count,
            "done_tasks": done_tasks,
        },
        "projects": projects_out,
        "status_distribution": status_distribution,
        "activity_7d": activity_7d,
    }

# ---- 同步自 service.py ----
def set_story_status(s: Session, id: int, new_status: str, *,
                     changed_by: int | None = None, reason: str = "") -> Story:
    """Story 强制迁移（Ticket 全流程）：单步查表 + blocked 全向可达。

    - 无 previous_status 恢复：Story 解除 blocked 仅允许 → todo / in_progress；
    - 变更即写 story_status_history；不变则 no-op。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    if new_status not in STORY_STATUSES:
        raise InvalidValue(f"invalid status '{new_status}'")
    old = st.status
    if old == new_status:
        s.refresh(st); return st
    if new_status != "blocked" and new_status not in STORY_TRANSITIONS.get(old, set()):
        raise IllegalTransition(f"{old} -> {new_status} 不合法")
    st.status = new_status
    _record_story_status_history(s, id, old, new_status, changed_by=changed_by, reason=reason)
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st

# ---- 同步自 service.py ----
def confirm_story(s: Session, id: int, *, changed_by: int | None = None) -> Story:
    """用户确认 Story 开始（Ticket 全流程人工闸门）：CAS backlog → confirmed。

    - 条件 UPDATE ``status=backlog`` → ``confirmed``，rowcount=1 才成功；
    - 幂等：已是 confirmed 直接返回（并发重放安全）；其它状态抛 IllegalTransition；
    - 确认后由 api 层发 MQ ``story.confirmed`` 触发 agent 自动处理编排。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    if st.status == "confirmed":
        s.refresh(st); return st
    if st.status != "backlog":
        raise IllegalTransition(f"story {id} 当前状态 {st.status}，仅 backlog 可确认开始")
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == "backlog")
        .values(status="confirmed")
    )
    if r.rowcount != 1:
        s.rollback()
        raise IllegalTransition("confirm 冲突：Story 状态已被并发修改")
    _record_story_status_history(s, id, "backlog", "confirmed", changed_by=changed_by,
                                 reason="用户确认开始")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st

# ---- 同步自 service.py ----
def complete_story(s: Session, id: int, *, changed_by: int | None = None,
                   reason: str = "") -> Story:
    """Story 自动收尾（Ticket 全流程）：任意非 done/blocked 状态 → done（CAS）。

    Worker 在 Story 下全部 task done 后调用本入口收尾，绕开常规迁移表
    （agent 推进中间态后可能停在 confirmed/todo/in_progress，直接置 done
    不在 TRANSITIONS 出边内）。blocked 不自动收尾（人工仲裁态）。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    if st.status == "done":
        s.refresh(st); return st
    if st.status == "blocked":
        raise IllegalTransition(f"story {id} 处于 blocked，禁止自动收尾（需人工仲裁）")
    old = st.status
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == old)
        .values(status="done")
    )
    if r.rowcount != 1:
        s.rollback()
        raise IllegalTransition("complete 冲突：Story 状态已被并发修改")
    _record_story_status_history(s, id, old, "done", changed_by=changed_by,
                                 reason=reason or "全部任务完成，自动收尾")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st

# ---- 同步自 service.py ----
def claim_story(s: Session, id: int, *, changed_by: int | None = None) -> Story:
    """Worker 竞争认领 Story（Ticket 全流程多实例编排）：CAS confirmed → todo。

    多个 Worker 实例（不同 agent CLI）同时扫描同一 confirmed Story 时，
    条件 UPDATE ``status=confirmed`` → ``todo``，rowcount=1 恰一赢家；
    竞争失败抛 IllegalTransition（api 层转 409）。todo 语义 = 已被某 worker
    认领处理中（其它实例扫描 confirmed 不再看到），失败/交接由
    ``unclaim_story`` 回退 confirmed 重新入池。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == "confirmed")
        .values(status="todo")
    )
    if r.rowcount != 1:
        s.rollback()
        cur = s.get(Story, id)
        if cur is not None and cur.status == "todo":
            raise IllegalTransition(f"story {id} 已被其它 Worker 认领（todo）")
        raise IllegalTransition(f"story {id} 当前状态 {cur.status if cur else '?'}，不可认领")
    _record_story_status_history(s, id, "confirmed", "todo", changed_by=changed_by,
                                 reason="Worker 竞争认领")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st

# ---- 同步自 service.py ----
def unclaim_story(s: Session, id: int, *, changed_by: int | None = None,
                  reason: str = "") -> Story:
    """Worker 认领交接/失败回退（Ticket 全流程）：CAS todo → confirmed。

    - agent 本轮未完成全部 task（部分推进）→ 回退 confirmed，下轮/其它实例再领；
    - agent 失败重试 → 回退 confirmed 重新入池（连续失败达上限转 blocked 不回退）。
    todo → confirmed 不在常规迁移表（todo 出边仅 in_progress/backlog/blocked），
    本入口为编排专用 CAS（同 complete_story 模式），blocked 不操作。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    if st.status == "blocked":
        raise IllegalTransition(f"story {id} 处于 blocked，禁止回退（需人工仲裁）")
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == "todo")
        .values(status="confirmed")
    )
    if r.rowcount != 1:
        s.rollback()
        cur = s.get(Story, id)
        if cur is not None and cur.status == "confirmed":
            raise IllegalTransition(f"story {id} 已是 confirmed")
        raise IllegalTransition(f"story {id} 当前状态 {cur.status if cur else '?'}，不可回退")
    _record_story_status_history(s, id, "todo", "confirmed", changed_by=changed_by,
                                 reason=reason or "Worker 交接/失败回退")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st

# ---- 同步自 service.py ----
def list_story_status_history(s: Session, story_id: int, limit: int = 100):
    """Story 状态变更历史（Ticket 全流程），按时间倒序返回。"""
    return (s.query(StoryStatusHistory)
            .filter(StoryStatusHistory.story_id == story_id)
            .order_by(StoryStatusHistory.id.desc())
            .limit(limit).all())

# ---- 同步自 service.py ----
def search_stories(s: Session, q: str, limit: int = 20):
    """全局 Story 关键词搜索（标题/描述），供命令面板等场景使用。"""
    like = f"%{q}%"
    qry = s.query(Story).filter(or_(Story.title.ilike(like), Story.description.ilike(like)))
    qry = qry.order_by(Story.id.desc())
    return qry.limit(limit).all()

# ---- 同步自 service.py ----
def search_epics(s: Session, q: str, limit: int = 20):
    """全局 Epic 关键词搜索（标题/描述），供命令面板等场景使用（Epic v6.13）。"""
    like = f"%{q}%"
    qry = s.query(Epic).filter(or_(Epic.title.ilike(like), Epic.description.ilike(like)))
    qry = qry.order_by(Epic.id.desc())
    return qry.limit(limit).all()

# ---- 同步自 service.py ----
def search_sprints(s: Session, q: str, limit: int = 20):
    """全局 Sprint 关键词搜索（title/goal），供命令面板等场景使用（v6.14）。"""
    like = f"%{q}%"
    qry = s.query(Sprint).filter(or_(Sprint.title.ilike(like), Sprint.goal.ilike(like)))
    qry = qry.order_by(Sprint.id.desc())
    return qry.limit(limit).all()

# ---- 同步自 service.py ----
def search_agents(s: Session, q: str, limit: int = 20):
    """全局 Agent 关键词搜索（agent_id/name/roles），供命令面板等场景使用（Epic 131 v6.16）。

    仅返回 enabled 的 Agent（已禁用/删除的不参与命令面板搜索）。
    """
    expire_stale_agent_heartbeats(s)
    like = f"%{q}%"
    qry = s.query(Agent).filter(
        Agent.enabled.is_(True),
        or_(
            Agent.agent_id.ilike(like),
            Agent.name.ilike(like),
            Agent.roles.ilike(like),
        ),
    )
    qry = qry.order_by(Agent.id.desc())
    return qry.limit(limit).all()

# ---- 同步自 service.py ----
def search_proposals(s: Session, q: str, limit: int = 20, user_id: int | None = None):
    """全局 Proposal 关键词搜索（title/content），供命令面板等场景使用（Epic 132 v6.17）。

    可见性收敛镜像 list_proposals：user_id 给定时，非 admin 仅搜索自己
    ProjectMember 项目下的提案；admin 或 None 全量。
    """
    like = f"%{q}%"
    qry = s.query(Proposal)
    if user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(Proposal.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    qry = qry.filter(or_(Proposal.title.ilike(like), Proposal.content.ilike(like)))
    qry = qry.order_by(Proposal.updated_at.desc(), Proposal.id.desc())
    return qry.limit(limit).all()

# ---- 同步自 service.py ----
def search_ticket_requests(s: Session, q: str, limit: int = 20, user_id: int | None = None):
    """全局 Ticket（Proposal→Ticket 转换请求）关键词搜索，供命令面板等场景使用（Epic 133 v6.18）。

    匹配字段：工单标题（title）/ 工单类型（type）/ 关联提案标题（Proposal.title，
    工单标题常为空默认用提案标题，join 已存在零额外成本）。

    可见性收敛镜像 search_proposals：user_id 给定时，非 admin 仅搜索自己
    ProjectMember 项目下提案关联的工单；admin 或 None 全量。

    返回 ``list[dict]``：``_ser(ProposalTicketRequest)`` 全列 + 附加 ``project_id``
    （工单表无该列，经提案反查，供前端显示项目名）。
    """
    like = f"%{q}%"
    qry = (
        s.query(ProposalTicketRequest, Proposal.project_id)
        .join(Proposal, Proposal.id == ProposalTicketRequest.proposal_id)
    )
    if user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(Proposal.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    qry = qry.filter(or_(
        ProposalTicketRequest.title.ilike(like),
        ProposalTicketRequest.type.ilike(like),
        Proposal.title.ilike(like),
    ))
    qry = qry.order_by(
        ProposalTicketRequest.updated_at.desc(),
        ProposalTicketRequest.id.desc(),
    )
    out: list[dict] = []
    for req, project_id in qry.limit(limit).all():
        d = _ser(req)
        d["project_id"] = project_id
        out.append(d)
    return out

# ---- 同步自 service.py ----
def search_schedules(s: Session, q: str, limit: int = 20, user_id: int | None = None):
    """全局定时计划（AgentSchedule）关键词搜索，供命令面板等场景使用（Epic 134 v6.19）。

    匹配字段：计划标题（title）/ 绑定 Agent（agent）/ 计划类型（schedule_type）。

    可见性收敛镜像 search_proposals：user_id 给定时，非 admin 仅搜索自己
    ProjectMember 项目下的定时计划；admin 或 None 全量。

    返回 ``list[AgentSchedule]``：AgentSchedule 自带 project_id 列，_ser 全列直接可用。
    """
    like = f"%{q}%"
    qry = s.query(AgentSchedule)
    if user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(AgentSchedule.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    qry = qry.filter(or_(
        AgentSchedule.title.ilike(like),
        AgentSchedule.agent.ilike(like),
        AgentSchedule.schedule_type.ilike(like),
    ))
    qry = qry.order_by(AgentSchedule.updated_at.desc(), AgentSchedule.id.desc())
    return qry.limit(limit).all()

# ---- 同步自 service.py ----
def search_runs(s: Session, q: str, limit: int = 20, user_id: int | None = None):
    """全局执行记录（AgentRun）关键词搜索，供命令面板等场景使用（Epic 135 v6.20）。

    匹配字段：运行状态（status）/ 执行摘要（summary）/ 错误信息（error_message）。
    AgentRun 无 project_id 列，通过 join AgentSchedule 取得归属项目做可见性收敛：
    可见性语义镜像 search_schedules —— user_id 给定时，非 admin 仅搜索自己
    ProjectMember 项目下的执行记录；admin 或 None 全量。

    返回 ``list[dict]``：_ser(run) 全列 + 附加 project_id（join 取得，供前端跳转）。
    """
    like = f"%{q}%"
    qry = (
        s.query(AgentRun, AgentSchedule.project_id)
        .join(AgentSchedule, AgentRun.schedule_id == AgentSchedule.id)
    )
    if user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(AgentSchedule.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    qry = qry.filter(or_(
        AgentRun.status.ilike(like),
        AgentRun.summary.ilike(like),
        AgentRun.error_message.ilike(like),
    ))
    qry = qry.order_by(AgentRun.id.desc())
    out: list[dict] = []
    for run, project_id in qry.limit(limit).all():
        d = _ser(run)
        d["project_id"] = project_id
        out.append(d)
    return out

# ---- 同步自 service.py ----
def list_run_records(
    s: Session,
    *,
    agent: str | None = None,
    status: str | None = None,
    q: str | None = None,
    task_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    user_id: int | None = None,
) -> dict:
    """Return enriched AgentRun rows for the Worker operations portal.

    AgentRun keeps the execution result while AgentSchedule, Task, Project and
    Agent provide the useful execution context.  The list intentionally emits
    only an output preview; callers can use ``GET /api/runs/{id}`` for the full
    output when an operator opens a row.
    """
    if status and status not in ALL_RUN_STATUSES:
        raise InvalidValue(f"invalid run status '{status}'")

    qry = (
        s.query(AgentRun, AgentSchedule, Task, Project, Agent)
        .join(AgentSchedule, AgentRun.schedule_id == AgentSchedule.id)
        .join(Project, AgentSchedule.project_id == Project.id)
        .outerjoin(Task, AgentRun.task_id == Task.id)
        .outerjoin(Agent, Agent.agent_id == func.coalesce(AgentRun.agent, AgentSchedule.agent))
    )

    if user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                row[0]
                for row in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            qry = (
                qry.filter(AgentSchedule.project_id.in_(member_pids))
                if member_pids else qry.filter(False)
            )

    if agent:
        qry = qry.filter(func.coalesce(AgentRun.agent, AgentSchedule.agent) == agent)
    if status:
        qry = qry.filter(AgentRun.status == status)
    if task_id is not None:
        qry = qry.filter(AgentRun.task_id == task_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(
            Task.title.ilike(like),
            Task.description.ilike(like),
            Task.spec.ilike(like),
            AgentSchedule.title.ilike(like),
            AgentRun.summary.ilike(like),
            AgentRun.output.ilike(like),
            AgentRun.error_message.ilike(like),
        ))

    total = qry.count()
    rows = (
        qry.order_by(AgentRun.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items: list[dict] = []
    default_agent = os.environ.get("AGENTBOARD_DEFAULT_AGENT", "codex")
    for run, schedule, task, project, agent_row in rows:
        item = _ser(run)
        output = item.pop("output", None) or ""
        description = ""
        if task is not None:
            description = (task.description or "").strip() or (task.spec or "").strip()
        started = run.started_at or run.created_at
        finished = run.finished_at
        duration_seconds = None
        if started and finished:
            duration_seconds = max(0, int((finished - started).total_seconds()))
        item.update({
            "project_id": project.id,
            "project_name": project.name,
            "project_key": project.key,
            "schedule_title": schedule.title,
            "agent": run.agent or schedule.agent or default_agent,
            "agent_name": agent_row.name if agent_row else None,
            "model": run.model or (agent_row.model if agent_row else ""),
            "task_title": task.title if task else schedule.title,
            "task_description": description,
            "output_preview": output[:1000],
            "has_output": bool(output),
            "duration_seconds": duration_seconds,
        })
        items.append(item)
    return {"items": items, "total": total}

# ---- 同步自 service.py ----
def list_project_kanban(s: Session, project_id: int,
                        include_all: bool = False) -> dict:
    """项目看板（Epic 130）：一个项目一个看板。

    - 默认只看 ``in_kanban=True`` 的 Story（ticket 标记进入看板）；
    - 每个 Story 附带其下 design/dev/qa task 的 status，供卡片展示三态；
    - 返回按 Story 状态分桶 + 全量列表（前端按状态渲染列）。
    """
    q = (s.query(Story)
         .join(Epic, Epic.id == Story.epic_id)
         .filter(Epic.project_id == project_id))
    if not include_all:
        q = q.filter(Story.in_kanban.is_(True))
    stories = q.order_by(Story.id.desc()).all()
    story_ids = [st.id for st in stories]
    tasks: list[Task] = []
    if story_ids:
        tasks = (s.query(Task)
                 .filter(Task.story_id.in_(story_ids))
                 .order_by(Task.id.asc())
                 .all())
    by_story: dict[int, list[dict]] = {}
    for t in tasks:
        by_story.setdefault(t.story_id, []).append({
            "id": t.id, "type": t.type, "title": t.title,
            "status": t.status, "priority": t.priority,
            "assignee_id": t.assignee_id, "estimate": t.estimate,
        })
    columns: dict[str, list[dict]] = {}
    for st in stories:
        col = columns.setdefault(st.status, [])
        col.append({
            "id": st.id, "epic_id": st.epic_id, "title": st.title,
            "description": st.description, "status": st.status,
            "needs_design": st.needs_design, "in_kanban": st.in_kanban,
            "tasks": by_story.get(st.id, []),
            "created_at": st.created_at,
        })
    return {"columns": columns, "items": [
        {"id": st.id, "epic_id": st.epic_id, "title": st.title,
         "status": st.status, "needs_design": st.needs_design,
         "in_kanban": st.in_kanban,
         "tasks": by_story.get(st.id, []),
         "created_at": st.created_at} for st in stories
    ]}

def get_agent_by_agent_id(s: Session, agent_id: str) -> Agent | None:
    return s.query(Agent).filter(Agent.agent_id == agent_id).first()
