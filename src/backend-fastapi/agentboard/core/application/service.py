"""AgentBoard service facade (Phase 9 cleanup, v3).

This module used to contain ~5300 lines of business logic. After the
Phase 1-4 vertical-slice refactor, business operations now live in
``agentboard.features.<X>.service``. This file is a thin facade that:

1. Re-exports the public exception classes (``DomainError``, ``NotFound``,
   ``Duplicate``, ``InvalidValue``, ``IllegalTransition``).
2. Re-exports a handful of legacy constants used by callers that import
   from ``service`` directly.
3. Keeps the underscore-prefixed helpers (``_ser``, ``_commit``, ``_paginate``,
   ``_check_status``, ``_check_type``, ``_required``, ``_parse_due_date``,
   ...) — these are still imported as ``service._ser(...)`` across the
   codebase (api.py, executor.py, all 10 feature routers).
4. Keeps any public function that has NOT been migrated to
   ``features/*/service`` (so external callers keep working).
5. Re-binds every public business function from ``features/*/service`` so
   old import paths (``from agentboard import service; service.create_task``)
   keep working — the new code path is the feature module.

If you only need the modern API, prefer importing directly from
``agentboard.features.<X>.service``.
"""
"""AgentBoard service facade (Phase 9 cleanup, v3).

This module used to contain ~5300 lines of business logic. After the
Phase 1-4 vertical-slice refactor, business operations now live in
``agentboard.features.<X>.service``. This file is a thin facade that:

1. Re-exports the public exception classes (``DomainError``, ``NotFound``,
   ``Duplicate``, ``InvalidValue``, ``IllegalTransition``).
2. Re-exports a handful of legacy constants used by callers that import
   from ``service`` directly.
3. Keeps the underscore-prefixed helpers (``_ser``, ``_commit``, ``_paginate``,
   ``_check_status``, ``_check_type``, ``_required``, ``_parse_due_date``,
   ...) — these are still imported as ``service._ser(...)`` across the
   codebase (api.py, executor.py, all 10 feature routers).
4. Keeps any public function that has NOT been migrated to
   ``features/*/service`` (so external callers keep working).
5. Re-binds every public business function from ``features/*/service`` so
   old import paths (``from agentboard import service; service.create_task``)
   keep working — the new code path is the feature module.

If you only need the modern API, prefer importing directly from
``agentboard.features.<X>.service``.
"""
"""AgentBoard service facade (Phase 9 cleanup, v3).

This module used to contain ~5300 lines of business logic. After the
Phase 1-4 vertical-slice refactor, business operations now live in
``agentboard.features.<X>.service``. This file is a thin facade that:

1. Re-exports the public exception classes (``DomainError``, ``NotFound``,
   ``Duplicate``, ``InvalidValue``, ``IllegalTransition``).
2. Re-exports a handful of legacy constants used by callers that import
   from ``service`` directly.
3. Keeps the underscore-prefixed helpers (``_ser``, ``_commit``, ``_paginate``,
   ``_check_status``, ``_check_type``, ``_required``, ``_parse_due_date``,
   ...) — these are still imported as ``service._ser(...)`` across the
   codebase (api.py, executor.py, all 10 feature routers).
4. Keeps any public function that has NOT been migrated to
   ``features/*/service`` (so external callers keep working).
5. Re-binds every public business function from ``features/*/service`` so
   old import paths (``from agentboard import service; service.create_task``)
   keep working — the new code path is the feature module.

If you only need the modern API, prefer importing directly from
``agentboard.features.<X>.service``.
"""
import json
import logging
import os
import random
import re
import traceback
from datetime import date, datetime, timedelta
from sqlalchemy import or_, and_, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ... import models, auth
from ...models import (
    ItemType, Status, Priority, SprintStatus, ALL_TYPES, ALL_STATUSES, ALL_STATUS_REASONS,
    STATUS_REASONS_BY_STATUS, StatusReason,
    ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES,
    Agent, Project, Epic, Story, Task, Comment, Sprint, Attachment, AgentSchedule, AgentRun,
    ProjectMember, Notification, User, ApiKey, AuditLog, TaskDependency, WebhookConfig,
    Document, DocumentComment, DocumentFolder, DocumentRevision, ReviewVote, TaskStatusHistory,
    Proposal, ProposalRound, ProposalQuestion, ProposalTicketRequest,
    StoryStatusHistory,
)
from ...domains.projects.models import STORY_REVIEW_STATUSES, STORY_STATUSES
from ...domains.documents.models import (
    DocumentStatus, DocumentType,
    ALL_DOCUMENT_TYPES, ALL_DOCUMENT_STATUSES, DOCUMENT_TRANSITIONS,
)
from ...domains.proposals.models import (
    ProposalStatus, ALL_PROPOSAL_STATUSES, PROPOSAL_TRANSITIONS, ASKABLE_STATUSES,
    CLAIMABLE_STATUSES, TICKET_TYPES, TICKET_REQUEST_STATUSES,
    TICKET_REQUEST_PENDING, TICKET_REQUEST_PROCESSING,
    TICKET_REQUEST_DONE, TICKET_REQUEST_FAILED,
)
from ...domains.proposals.state_machine import (
    IllegalTransitionError as _SM_IllegalTransitionError,
    ProposalStateMachine,
    TransitionSpec,
    bind_side_effects,
)
from ...domains.common.models import utc_now
from ..exceptions import (  # noqa: E402
    DomainError, NotFound, IllegalTransition, Duplicate, InvalidValue,
    # T1.5：执行门 403。路由 `except service.Forbidden` 需要从 facade 拿得到，
    # 否则 ImportError/AttributeError 会在**运行时**才炸。
    Forbidden,
)
from ..service_helpers import _parse_json_list, validate_cli_command  # noqa: E402,F401

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200

log = logging.getLogger(__name__)

# 任务终态：done / blocked（Epic 140 学习 outcome + 异步 judge 仅在终态触发）。
# 顶层 facade 与 features.learning.service 共享语义，故在此显式常量导出，
# 避免跨模块访问下划线私有常量。
_TERMINAL_STATUSES = {Status.DONE, Status.BLOCKED}

def _parse_due_date(value):
    """Convert ISO date string (YYYY-MM-DD) to date object; pass through None/date."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise InvalidValue(f"invalid due_date format: {value!r}, expected YYYY-MM-DD")

# 合法状态迁移（Story 265 收敛后）
# 5 状态机：todo / in_progress / in_review / done / blocked
# 设计评审段与最终评审已下线，design 任务走通用 todo→in_progress→in_review→done 流。
#
# 关键规则：
# - 任意非终态 → blocked 全向可达（set_status 统一特判）；
# - 解除 blocked 恢复到 previous_status（set_status 动态处理，不在此表特判）；
# - re-open：done → in_progress（status_reason 自动清空）。
TRANSITIONS = {
    Status.TODO: {Status.IN_PROGRESS, Status.DONE, Status.BLOCKED},
    Status.IN_PROGRESS: {Status.IN_REVIEW, Status.TODO, Status.DONE, Status.BLOCKED},
    Status.IN_REVIEW: {Status.DONE, Status.IN_PROGRESS, Status.BLOCKED},
    Status.DONE: {Status.IN_PROGRESS, Status.BLOCKED},
    Status.BLOCKED: {Status.TODO, Status.IN_PROGRESS, Status.IN_REVIEW},
}

def transitions_for(needs_design: bool) -> dict:
    """返回任务适用的迁移表（Story 265 后所有 Story 走同一张 5 状态表）。

    needs_design 形参保留以兼容旧调用方（现在不影响表内容）；
    blocked 全向可达与解除恢复 previous_status 由 set_status 动态处理，不在此表特判。
    """
    return TRANSITIONS

# Story 强制迁移（Ticket 全流程，2026-08-09）：单步查表 + blocked 全向特判（set_story_status）。
# 与 Task 的 TRANSITIONS 同模式；Story 无 previous_status 字段,
# 解除 blocked 默认建议回 todo/in_progress,但用户可按实际原因选任何目标态
# (如 in_review 阶段被外部 reviewer 不可用 block,解除时直接进 verifying 重新指派)。
# confirmed/backlog 是闸门态,通常不会从 blocked 转入(走 confirm_story 专用入口),
# 但代码层保留全 8 态可达以避免用户被强约束卡住。
STORY_TRANSITIONS: dict[str, set[str]] = {
    "backlog":     {"confirmed", "blocked"},
    "confirmed":   {"todo", "blocked"},
    "todo":        {"in_progress", "backlog", "blocked"},
    "in_progress": {"in_review", "todo", "blocked"},
    "in_review":   {"verifying", "done", "in_progress", "blocked"},
    "verifying":   {"done", "in_progress", "blocked"},
    "done":        {"in_progress", "todo", "blocked"},
    "blocked":     {"backlog", "confirmed", "todo", "in_progress",
                    "in_review", "verifying", "done"},
}

EDITABLE = {
    "name", "key", "description", "is_private",   # project
    "title", "description", "status",      # epic / story / task
    "type", "spec", "priority", "sprint_id",  # task
    # Epic 17: 任务管理增强
    "assignee_id", "due_date", "labels", "estimate",
}

def _ser(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[c.name] = v
    return out

def _required(value: str, field: str, max_length: int) -> str:
    value = (value or "").strip()
    if not value:
        raise InvalidValue(f"{field} is required")
    if len(value) > max_length:
        raise InvalidValue(f"{field} must be at most {max_length} characters")
    return value

def _check_type(value: str) -> None:
    if value not in ALL_TYPES:
        raise InvalidValue(f"invalid type '{value}'")

def _check_status(value: str) -> None:
    if value not in ALL_STATUSES:
        raise InvalidValue(f"invalid status '{value}'")

def _paginate(q, limit: int | None, offset: int):
    if offset < 0:
        raise InvalidValue("offset must be non-negative")
    actual_limit = DEFAULT_PAGE_SIZE if limit is None else limit
    if actual_limit < 1 or actual_limit > MAX_PAGE_SIZE:
        raise InvalidValue(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    return q.limit(actual_limit).offset(offset)

def _commit(s: Session, *, duplicate: str | None = None) -> None:
    try:
        s.flush()
        if s.info.get("auto_commit", True):
            s.commit()
    except IntegrityError as exc:
        s.rollback()
        if duplicate:
            raise Duplicate(duplicate) from exc
        raise InvalidValue("database constraint violated") from exc

# ---------- Project ----------

# ---------- Epic ----------

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

# ---------- Story ----------

def search_stories(s: Session, q: str, limit: int = 20):
    """全局 Story 关键词搜索（标题/描述），供命令面板等场景使用。"""
    like = f"%{q}%"
    qry = s.query(Story).filter(or_(Story.title.ilike(like), Story.description.ilike(like)))
    qry = qry.order_by(Story.id.desc())
    return qry.limit(limit).all()

def search_epics(s: Session, q: str, limit: int = 20):
    """全局 Epic 关键词搜索（标题/描述），供命令面板等场景使用（Epic v6.13）。"""
    like = f"%{q}%"
    qry = s.query(Epic).filter(or_(Epic.title.ilike(like), Epic.description.ilike(like)))
    qry = qry.order_by(Epic.id.desc())
    return qry.limit(limit).all()

def search_sprints(s: Session, q: str, limit: int = 20):
    """全局 Sprint 关键词搜索（title/goal），供命令面板等场景使用（v6.14）。"""
    like = f"%{q}%"
    qry = s.query(Sprint).filter(or_(Sprint.title.ilike(like), Sprint.goal.ilike(like)))
    qry = qry.order_by(Sprint.id.desc())
    return qry.limit(limit).all()

def search_agents(s: Session, q: str, limit: int = 20):
    """全局 Agent 关键词搜索（agent_id/name/roles），供命令面板等场景使用（Epic 131 v6.16）。

    仅返回 enabled 的 Agent（已禁用/删除的不参与命令面板搜索）。
    """
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

def _record_story_status_history(s: Session, story_id: int, from_status: str, to_status: str,
                                 *, changed_by: int | None = None, reason: str = "") -> None:
    """Story 状态变更历史（story_status_history）：全部状态变更路径统一调用。"""
    s.add(StoryStatusHistory(
        story_id=story_id, from_status=from_status, to_status=to_status,
        changed_by=changed_by, reason=reason or "",
    ))

def list_story_status_history(s: Session, story_id: int, limit: int = 100):
    """Story 状态变更历史（Ticket 全流程），按时间倒序返回。"""
    return (s.query(StoryStatusHistory)
            .filter(StoryStatusHistory.story_id == story_id)
            .order_by(StoryStatusHistory.id.desc())
            .limit(limit).all())

def set_story_status(s: Session, id: int, new_status: str, *,
                     changed_by: int | None = None, reason: str = "") -> Story:
    """Story 强制迁移（Ticket 全流程）：单步查表 + blocked 全向可达。

    - blocked 解除默认建议回 todo / in_progress,但 STORY_TRANSITIONS["blocked"]
      已放宽到 8 态全可达,用户可按实际原因选任何目标态(如 in_review 阶段
      reviewer 不可用导致 block,解除时直接进 verifying 重新指派);
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

# ---------- Agent 注册表（Epic 122 S1） ----------
# _parse_json_list 已迁移至 core.service_helpers（Phase 9），此处保留旧引用：
# 由下方 helper import 统一提供，避免本地重复定义分叉。

def get_agent_by_agent_id(s: Session, agent_id: str) -> Agent | None:
    return s.query(Agent).filter(Agent.agent_id == agent_id).first()

def update_agent(s: Session, agent_id: str, **fields) -> Agent | None:
    """前端配置中心更新 Agent（PUT /api/agents/{agent_id}）。

    可更新：name/roles/capabilities/cli_command/model/enabled/user_id。
    """
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    if "name" in fields and fields["name"] is not None:
        agent.name = _required(fields["name"], "name", 100)
    if "roles" in fields and fields["roles"] is not None:
        agent.roles = json.dumps(_parse_json_list(fields["roles"], "roles"),
                                 ensure_ascii=False)
    if "capabilities" in fields and fields["capabilities"] is not None:
        agent.capabilities = json.dumps(
            _parse_json_list(fields["capabilities"], "capabilities"), ensure_ascii=False)
    if "cli_command" in fields and fields["cli_command"] is not None:
        # B-A2: cli_command 安全校验（防 shell 注入，与 probe dry-run 配合）
        validate_cli_command(fields["cli_command"])
        agent.cli_command = str(fields["cli_command"] or "")[:500]
    if "model" in fields and fields["model"] is not None:
        agent.model = str(fields["model"] or "")[:100]
    if "enabled" in fields and fields["enabled"] is not None:
        agent.enabled = bool(fields["enabled"])
    if "user_id" in fields:
        uid = fields["user_id"]
        if uid is not None and not s.get(User, uid):
            raise NotFound(f"user {uid} not found")
        agent.user_id = uid
    _commit(s); s.refresh(agent); return agent

def delete_agent(s: Session, agent_id: str) -> Agent | None:
    """删除 Agent 注册记录（前端配置中心）。"""
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    s.delete(agent)
    _commit(s)
    return agent

# ---------- Story 评审闭环（Epic 122 S1） ----------
# 多数决评审的常量与私有函数（REVIEW_MODE_* / DEFAULT_REVIEW_QUORUM /
# get_review_* / _vote_majority / _settle_* / _online_reviewer_candidates /
# _reassign_*_reviewer）真源已迁至 features/scheduling/service.py，
# 本模块末尾统一转发 —— 详见文件末尾「评审真源统一」注释块。
# MAX_REVIEW_ROUNDS 同样转发（原在此处第三份定义，2026-09-02 按 Plan §六-4
# 收敛到 scheduling/models.py 一处）。

def list_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的评审任务（Story，按 pending_review 优先排序）。"""
    q = s.query(Story).filter(Story.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES and status not in STORY_REVIEW_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Story.status == status)
    q = q.order_by(Story.status.desc(), Story.id.desc())
    return q.all()

# ---------- Task ----------

def update_task(s: Session, id: int, **fields) -> Task | None:
    """原子 PATCH：所有字段 + 状态变更在同一事务里 commit，避免 partial commit。

    Story 265 回归：旧实现先 setattr 非状态字段 + ``_commit``，再调 ``set_status``
    走状态机 —— 一旦状态机抛 ``IllegalTransition``（如 done→todo），非状态字段已
    commit，形成 partial commit。修复时**整函数 0 次中间 commit**、单次 commit
    收口，任何字段校验/状态机迁移失败都让外层 session 回滚，**绝不留半成品**。
    """
    t = s.get(Task, id)
    if not t:
        return None
    allowed = {"title", "description", "spec", "type", "status", "priority", "sprint_id",
               "assignee_id", "due_date", "labels", "estimate", "needed_capabilities",
               "complexity", "domain_tags", "assignment_mode",
               "created_by_user_id"}  # Epic 17 / Epic 32；created_by_user_id 供人工补 owner（决策 c）
    nullable_fields = {"due_date", "sprint_id", "assignee_id", "estimate", "complexity",
                       "created_by_user_id"}  # fields that can be set to None
    # 抽出 status/status_reason：状态变更必须走状态机（execute_transition 包装），
    # 在事务末与其它字段一起 commit。
    new_status = fields.pop("status", None)
    new_status_reason = fields.pop("status_reason", None)  # Story 265：done/blocked 必填
    status_changed = new_status is not None and new_status != t.status

    # ---- 阶段 1：校验 + 应用非状态字段（不 commit）----
    for k, v in fields.items():
        if k not in allowed:
            continue
        if v is None and k not in nullable_fields:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "priority":
            _check_priority(v)
        elif k == "type":
            _check_type(v)
        elif k == "sprint_id":
            if v is not None:
                sp = s.get(Sprint, v)
                if not sp or sp.project_id != t.project_id:
                    raise InvalidValue(f"sprint {v} does not belong to project {t.project_id}")
                if sp.status == SprintStatus.COMPLETED:
                    raise InvalidValue("cannot assign task to a completed sprint")
        elif k == "assignee_id":
            if v is not None:
                user = s.get(User, v)
                if not user:
                    raise InvalidValue(f"assignee {v} not found")
        elif k == "due_date":
            v = _parse_due_date(v)
        elif k == "labels":
            import json
            try:
                json.loads(v)
            except json.JSONDecodeError:
                raise InvalidValue("labels must be a valid JSON array")
        elif k == "needed_capabilities":
            from ...features.scheduling.matching import normalize_required_capabilities
            v = json.dumps(normalize_required_capabilities(v), ensure_ascii=False)
        elif k == "complexity":
            from ...features.scheduling.matching import normalize_complexity
            v = normalize_complexity(v)
        elif k == "domain_tags":
            from ...features.scheduling.matching import normalize_domain_tags
            v = json.dumps(normalize_domain_tags(v), ensure_ascii=False)
        elif k == "assignment_mode":
            from ...features.scheduling.matching import normalize_assignment_mode
            v = normalize_assignment_mode(v)
        setattr(t, k, v)

    # ---- 阶段 2：状态迁移前置 dry-run（不 commit）----
    # 状态机的 ``_validate_status_reason`` 会读 ``t.status_reason``，所以
    # 先把新 reason 写到 ORM 对象（仍在事务内），让 validator 看到最终值。
    # 这一步只在内存中改，**不调 execute_transition** —— 真正的迁移在 commit
    # 前统一做，保证事务原子性。
    if status_changed:
        # 先验 status_reason 合法性，raise 会让外层回滚（不会到 commit）
        t.status_reason = _validate_status_reason(Status(new_status), new_status_reason)

    # ---- 阶段 3：状态机迁移（不 commit）----
    # execute_transition 写 history + 维护 previous_status + 失效缓存
    # 全部走 side effects，不涉及 commit。
    if status_changed:
        from ...features.work_items.state_machine import execute_transition
        execute_transition(s, t, new_status, reason="patch")

    # ---- 阶段 4：单次 commit（事务原子边界）----
    # 任何上面 raise 的 InvalidValue/IllegalTransition 都会让外层 session
    # 回滚（请求级事务由 middleware 处理，测试 fixture 显式 rollback），
    # 不会出现"title 已改但 status 没动"的 partial commit。
    _commit(s)
    s.refresh(t)

    # ---- 阶段 5：post-commit 副作用（仅成功路径跑）----
    # 学习 outcome 落库 + 异步 judge 调度：终态走；非终态不落 outcome 但不报错。
    # 与 ``set_status``（features.work_items.service）保持等价行为：
    # 终态 → outcome 同步落库 + 异步 judge；非终态只走 outcome（不触发 judge）。
    if status_changed and t.status in _TERMINAL_STATUSES:
        try:
            from ...features.work_items.service import finalize_task_assignment
            finalize_task_assignment(s, t)
            from ...features.work_items.service import _record_learning_outcome
            _record_learning_outcome(s, t)
        except Exception:
            log.warning("update_task: learning outcome failed for task#%s", t.id, exc_info=True)
        # 异步 L3 judge：daemon 线程 + 独立 Session；失败吞异常。
        if os.environ.get("AGENTBOARD_JUDGE_AUTO", "1") == "1":
            try:
                from ...features.learning.judge import schedule_judge
                schedule_judge(t.id)
            except Exception:
                log.debug("update_task: schedule_judge enqueue failed for task#%s", t.id)
    # 关键字段变更时清除项目统计缓存（Epic 23 Story 23.1）
    if any(k in fields for k in ("status", "sprint_id", "priority")) or status_changed:
        _invalidate_project_stats_cache(t.project_id)
    return t

def delete_task(s: Session, id: int) -> bool:
    """删除 task + 清理所有指向它的外键引用（FK 防御性级联）。

    **根因说明**：Epic 140 切片 1/3 引入 ``task_outcome`` / ``episode_embedding`` /
    ``project_playbook`` 三张表（FK → tasks.id，NO ACTION），旧 facade 的
    ``delete_task`` 没跟进清理这些引用，导致"task 走到 done → 落 outcome + episode
    → 用户再删 task"路径撞 FK 约束抛 IntegrityError → HTTP 422。这是真实回
    归路径，~~PRE-EXISTING~~（实为切片 1+3 拆出后未同步），在此一次性补齐。

    清理策略（与既有 model.delete 风格一致）：
    - 1:N / N:M 引用（history / dependency / attachment / comment / outcome /
      episode / project_playbook_episode）：硬删；
    - N:1 反向引用（agent_run.task_id / task.source_spec_id）：置 NULL 保留审计；
    - N:1 反向引用（project_playbook.last_appended_episode_id）：置 NULL，
      旧 playbook 内容仍保留（不去重历史记录，避免误删用户整理的 pattern）。
    """
    t = s.get(Task, id)
    if not t:
        return False
    pid = t.project_id
    s.query(AgentRun).filter(AgentRun.task_id == id).update(
        {AgentRun.task_id: None}, synchronize_session=False,
    )
    s.query(Task).filter(Task.source_spec_id == id).update(
        {Task.source_spec_id: None}, synchronize_session=False,
    )
    s.query(TaskDependency).filter(or_(
        TaskDependency.task_id == id,
        TaskDependency.depends_on_id == id,
    )).delete(synchronize_session=False)
    s.query(Attachment).filter(Attachment.task_id == id).delete(synchronize_session=False)
    # review_votes.comment_id → comments.id（NO ACTION）：删 task comment 前
    # 必须先解绑引用，避免 ``FOREIGN KEY constraint failed``。先扫描要删的
    # comment_id 列表，再 update 引用 + delete comment。
    task_comment_ids = [
        x[0] for x in s.query(Comment.id).filter(Comment.task_id == id).all()
    ]
    if task_comment_ids:
        s.query(ReviewVote).filter(
            ReviewVote.comment_id.in_(task_comment_ids)
        ).update({ReviewVote.comment_id: None}, synchronize_session=False)
    s.query(Comment).filter(Comment.task_id == id).delete(synchronize_session=False)
    # Epic 140 切片 1：终态能力评分（task_id 唯一，硬删）
    try:
        from ...features.learning.models import (
            EpisodeEmbedding, ProjectPlaybook, ProjectPlaybookEpisode, TaskOutcome,
        )
        s.query(TaskOutcome).filter(TaskOutcome.task_id == id).delete(
            synchronize_session=False,
        )
        # Epic 140 切片 3：episode 向量化快照（episode_id=task_id 唯一，硬删）
        s.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == id).delete(
            synchronize_session=False,
        )
        # project_playbook.last_appended_episode_id：置 NULL 保留 playbook 内容
        # （不去重历史 pattern，避免误删用户整理的经验；旧 entry 仍可读）
        s.query(ProjectPlaybook).filter(
            ProjectPlaybook.last_appended_episode_id == id,
        ).update(
            {ProjectPlaybook.last_appended_episode_id: None},
            synchronize_session=False,
        )
        # project_playbook_episode：playbook 幂等锚点（task 被删则锚点失效，硬删）
        # 保留 project_playbook.content_md 不动——episode 数据已不存在但历史 pattern
        # 仍可读，符合「不去重历史」原则。
        s.query(ProjectPlaybookEpisode).filter(
            ProjectPlaybookEpisode.episode_id == id,
        ).delete(synchronize_session=False)
    except Exception:  # noqa: BLE001
        # 防御：万一 learning 表不存在（极老的 DB），不影响主删除流程
        log.warning("delete_task: learning cleanup skipped for task#%s", id, exc_info=True)
    s.delete(t); _commit(s)
    _invalidate_project_stats_cache(pid)
    return True

def set_task_description(s: Session, id: int, text: str) -> Task | None:
    return update_task(s, id, description=text)

def set_task_spec(s: Session, id: int, text: str) -> Task | None:
    return update_task(s, id, spec=text)

def append_task_spec(s: Session, id: int, text: str) -> Task | None:
    t = s.get(Task, id)
    if not t:
        return None
    t.spec = (t.spec or "") + "\n" + text
    _commit(s); s.refresh(t); return t

def _task_needs_design(s: Session, t: Task) -> bool:
    """Task 所属 Story 是否需要设计评审段（Epic 123）；无 Story 视为快速流（false）。"""
    if t.story_id is None:
        return False
    story = s.get(Story, t.story_id)
    return bool(story and story.needs_design)

def _record_status_history(s: Session, task_id: int, from_status: str, to_status: str,
                           *, changed_by: int | None = None, reason: str = "") -> None:
    """任务状态变更历史（task_status_history）：全部状态变更路径统一调用。"""
    s.add(TaskStatusHistory(
        task_id=task_id, from_status=from_status, to_status=to_status,
        changed_by=changed_by, reason=reason or "",
    ))

def _validate_status_reason(new_status: Status, status_reason: str | None) -> str | None:
    """校验并规范化 status_reason（Story 265）。

    - new=done: 必填，必须是 completed/withdrawn 之一；
    - new=blocked: 必填，必须是 4 个 blocked reason 之一；
    - 其他状态：清空（不持久化）。
    """
    allowed = STATUS_REASONS_BY_STATUS.get(str(new_status))
    if allowed is None:
        # 非 done/blocked：清空 reason
        return None
    if not status_reason:
        raise InvalidValue(
            f"status_reason is required for status={new_status}; "
            f"allowed: {sorted(allowed)}"
        )
    if status_reason not in allowed:
        raise InvalidValue(
            f"invalid status_reason '{status_reason}' for status={new_status}; "
            f"allowed: {sorted(allowed)}"
        )
    return status_reason

# ---------- Task 评审闭环（Epic 122 切片 2 M2） ----------

def list_task_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的 Task 评审任务（按 in_review 优先排序）。"""
    q = s.query(Task).filter(Task.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Task.status == status)
    q = q.order_by(Task.status.desc(), Task.id.desc())
    return q.all()

# ---------- 评审统计与超时护栏（Epic 122 S3 M2） ----------
DEFAULT_REVIEW_TIMEOUT_MINUTES = 30
DEFAULT_TIMEOUT_SCAN_BATCH = 20

def _story_last_activity(s: Session, story: Story) -> datetime:
    """Story 最后活动 = max(created_at, 最新评论时间)；无评论回退 created_at。

    评审意见唯一载体是评论（评论往返即活动），Story 无 updated_at 列，
    用评论时间作为「卡住多久」的代理指标（零迁移方案）。
    """
    last_comment = s.query(func.max(Comment.created_at)).filter(
        Comment.story_id == story.id
    ).scalar()
    if last_comment is not None and last_comment > story.created_at:
        return last_comment
    return story.created_at

def get_review_stats(s: Session, *, project_id: int, days: int = 7,
                     user_id: int | None = None) -> dict:
    """项目级评审统计运营视图（S3 M2）。

    口径（见 design.md §4）：
    - story approved = status=ready 且 reviewer 已指派；rejected = review_round>0；
      pending = pending_review；blocked = blocked；
    - task approved = done 且 reviewer 已指派；rejected = review_round>0；
      pending = in_review；blocked = blocked；
    - reject_rate = rejected / (approved + rejected)，分母 0 → 0.0；
    - by_reviewer：按 reviewer_id 聚合评审工作量（approve/reject 分布）；
    - days 过滤 created_at ≥ now-days；user_id 过滤仅统计该评审人条目。
    """
    project = s.get(Project, project_id)
    if not project:
        raise NotFound(f"project {project_id} not found")
    days = max(0, int(days)) if days is not None else 7
    since = utc_now() - timedelta(days=days) if days > 0 else None

    st_q = s.query(Story).join(Epic, Story.epic_id == Epic.id).filter(
        Epic.project_id == project_id)
    t_q = s.query(Task).filter(Task.project_id == project_id)
    if since is not None:
        st_q = st_q.filter(Story.created_at >= since)
        t_q = t_q.filter(Task.created_at >= since)
    if user_id is not None:
        st_q = st_q.filter(Story.reviewer_id == user_id)
        t_q = t_q.filter(Task.reviewer_id == user_id)
    stories = st_q.all()
    tasks = t_q.all()

    def _buckets(items, *, is_story):
        approved = rejected = pending = blocked = 0
        rounds, round_n = 0, 0
        for it in items:
            status = it.status
            reviewed = it.reviewer_id is not None or (it.review_round or 0) > 0
            if is_story:
                if status == "ready" and it.reviewer_id is not None:
                    approved += 1
                if status == "pending_review":
                    pending += 1
            else:
                if status == Status.DONE and it.reviewer_id is not None:
                    approved += 1
                if status == Status.IN_REVIEW:
                    pending += 1
            if status == Status.BLOCKED or status == "blocked":
                blocked += 1
            if (it.review_round or 0) > 0:
                rejected += 1
            if reviewed:
                rounds += it.review_round or 0
                round_n += 1
        return {
            "total": len(items),
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "blocked": blocked,
            "_rounds": rounds,
            "_round_n": round_n,
        }

    sb = _buckets(stories, is_story=True)
    tb = _buckets(tasks, is_story=False)

    def _avg(b):
        return round(b["_rounds"] / b["_round_n"], 2) if b["_round_n"] else 0.0

    # timeout_pending：当前超时未决数（默认 30min 口径）
    timeout = timedelta(minutes=DEFAULT_REVIEW_TIMEOUT_MINUTES)
    now = utc_now()
    timeout_pending = 0
    for st in stories:
        if st.status == "pending_review" and st.reviewer_id is not None \
                and now - _story_last_activity(s, st) > timeout:
            timeout_pending += 1
    for t in tasks:
        if t.status == Status.IN_REVIEW and t.reviewer_id is not None \
                and now - t.updated_at > timeout:
            timeout_pending += 1

    # by_reviewer 聚合
    agg: dict[int, dict] = {}
    for it in list(stories) + list(tasks):
        rid = it.reviewer_id
        if rid is None:
            continue
        row = agg.setdefault(rid, {
            "user_id": rid, "name": None,
            "story_reviewed": 0, "task_reviewed": 0,
            "story_approved": 0, "story_rejected": 0,
            "task_approved": 0, "task_rejected": 0,
        })
        if it.__class__ is Story:
            row["story_reviewed"] += 1
            if it.status == "ready":
                row["story_approved"] += 1
            if (it.review_round or 0) > 0:
                row["story_rejected"] += 1
        else:
            row["task_reviewed"] += 1
            if it.status == Status.DONE:
                row["task_approved"] += 1
            if (it.review_round or 0) > 0:
                row["task_rejected"] += 1
    by_reviewer = []
    for rid, row in agg.items():
        u = s.get(User, rid)
        row["name"] = u.display_name or u.username if u else f"user#{rid}"
        by_reviewer.append(row)
    by_reviewer.sort(key=lambda r: -(r["story_reviewed"] + r["task_reviewed"]))

    # S4 M2：多数决评审投票进度（review_mode/quorum/votes）
    # - review_mode：single|majority（env 驱动）；review_quorum：法定票数；
    # - majority 模式下 votes 列出全部 pending 实体（pending_review Story /
    #   in_review Task）的已投票数（approve/reject/cast）与 quorum；
    # - single 模式 votes 恒为空数组（零行为变化）。
    review_mode = get_review_mode()
    review_quorum = get_review_quorum()
    vote_rows: list[dict] = []
    if review_mode == REVIEW_MODE_MAJORITY:
        for st in stories:
            if st.status != "pending_review":
                continue
            approve_n, reject_n = _review_vote_counts(s, "story", st.id)
            vote_rows.append({
                "kind": "story",
                "id": st.id,
                "title": st.title,
                "status": st.status,
                "approve": approve_n,
                "reject": reject_n,
                "cast": approve_n + reject_n,
                "quorum": review_quorum,
            })
        for t in tasks:
            if t.status != Status.IN_REVIEW:
                continue
            approve_n, reject_n = _review_vote_counts(s, "task", t.id)
            vote_rows.append({
                "kind": "task",
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "approve": approve_n,
                "reject": reject_n,
                "cast": approve_n + reject_n,
                "quorum": review_quorum,
            })

    total_done = sb["approved"] + sb["rejected"] + tb["approved"] + tb["rejected"]
    total_rejected = sb["rejected"] + tb["rejected"]
    return {
        "project_id": project_id,
        "days": days,
        "stories": {k: sb[k] for k in ("total", "approved", "rejected", "pending", "blocked")},
        "tasks": {k: tb[k] for k in ("total", "approved", "rejected", "pending", "blocked")},
        "rounds": {"avg_story_round": _avg(sb), "avg_task_round": _avg(tb)},
        "reject_rate": round(total_rejected / total_done, 4) if total_done else 0.0,
        "timeout_pending": timeout_pending,
        "by_reviewer": by_reviewer,
        "review_mode": review_mode,
        "review_quorum": review_quorum,
        "votes": vote_rows,
        "generated_at": utc_now().isoformat(),
    }

def _invalidate_project_stats_cache(project_id: int) -> None:
    """清除项目统计缓存（Epic 23 Story 23.1）"""
    try:
        from agentboard.cache import get_cache
        cache = get_cache()
        cache.delete(f"project_stats:{project_id}")
    except Exception:
        pass  # 缓存失败不影响主流程

# ---------- Spec -> 子任务（OpenSpec / Superpowers 风格） ----------
#
# T1.5：本文件此前有一份 generate_tasks_from_spec 的重复实现，且与
# features/work_items/service.py 的版本**分叉**（那版会继承 created_by_*，
# 这版不继承）。可达性分析：router 走 `core.application.service` facade，
# 命中的是 core 这份 —— 也就是说线上跑的一直是功能更少的那版。
#
# 按 T0.1a 同款做法收敛：删掉 core 本地定义，统一转发到 features（真源）。
# 转发发生在文件末尾的重绑定区，晚于本位置，因此对外符号不变。
# ---------------------------------------------------------------------------

# ---------- Search ----------
def search_tasks(s: Session, *, project_id=None, epic_id=None, story_id=None,
                 sprint_id=None, type=None, status=None, priority=None, q=None,
                 reviewer_id: int | None = None,
                 limit: int | None = None, offset: int = 0,
                 project_ids: list[int] | None = None):
    """跨项目任务搜索。

    ``project_ids``（T2.1 读门）：只返回这些 project 的 task；``None`` 表示
    不加这层过滤（admin / 内部调用）。与 ``project_id``（单项目精确查询）
    是两个维度 —— 前者是**权限边界**，后者是**查询条件**，不要混用。
    """
    qry = s.query(Task)
    if project_id is not None:
        qry = qry.filter(Task.project_id == project_id)
    if project_ids is not None:
        # 空列表 = 该用户一个项目都读不了 → 显式返回空，不能漏掉这层过滤
        qry = qry.filter(Task.project_id.in_(project_ids)) if project_ids \
            else qry.filter(False)
    if story_id is not None:
        qry = qry.filter(Task.story_id == story_id)
    if sprint_id is not None:
        qry = qry.filter(Task.sprint_id == sprint_id)
    if type is not None:
        _check_type(type)
        qry = qry.filter(Task.type == type)
    if status is not None:
        _check_status(status)
        qry = qry.filter(Task.status == status)
    if priority is not None:
        _check_priority(priority)
        qry = qry.filter(Task.priority == priority)
    if reviewer_id is not None:
        qry = qry.filter(Task.reviewer_id == reviewer_id)
    if epic_id is not None:
        qry = qry.join(Story, Task.story_id == Story.id).filter(Story.epic_id == epic_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Task.title.ilike(like), Task.description.ilike(like),
                              Task.spec.ilike(like)))
    qry = qry.order_by(Task.id.desc())
    return _paginate(qry, limit, offset).all()

def _check_priority(priority: str) -> None:
    if priority not in ALL_PRIORITIES:
        raise InvalidValue(f"invalid priority '{priority}'")

# ---------- Comments ----------
def _comment_target(
    s: Session, *, task_id: int | None, story_id: int | None, epic_id: int | None
) -> dict:
    """校验评论挂载目标：task/story/epic 三者恰好其一非空，且实体存在。返回 {task_id|story_id|epic_id: id}。"""
    candidates = {"task_id": (Task, task_id), "story_id": (Story, story_id), "epic_id": (Epic, epic_id)}
    present = {k: v for k, v in candidates.items() if v[1] is not None}
    if len(present) != 1:
        raise InvalidValue("exactly one of task_id/story_id/epic_id must be set")
    name, (model, obj_id) = next(iter(present.items()))
    if not s.get(model, obj_id):
        raise NotFound(f"{name.removesuffix('_id')} {obj_id} not found")
    return {name: obj_id}

def create_comment(s: Session, *, author: str, content: str,
                   task_id: int | None = None, story_id: int | None = None,
                   epic_id: int | None = None) -> Comment:
    target = _comment_target(s, task_id=task_id, story_id=story_id, epic_id=epic_id)
    author, content = (author or "").strip(), (content or "").strip()
    if not author or not content:
        raise InvalidValue("author and content are required")
    comment = Comment(author=author[:100], content=content, **target)
    s.add(comment); _commit(s); s.refresh(comment); return comment

def list_comments(s: Session, *, task_id: int | None = None, story_id: int | None = None,
                  epic_id: int | None = None):
    if task_id is not None:
        if not s.get(Task, task_id):
            raise NotFound(f"task {task_id} not found")
        q = s.query(Comment).filter(Comment.task_id == task_id)
    elif story_id is not None:
        if not s.get(Story, story_id):
            raise NotFound(f"story {story_id} not found")
        q = s.query(Comment).filter(Comment.story_id == story_id)
    elif epic_id is not None:
        if not s.get(Epic, epic_id):
            raise NotFound(f"epic {epic_id} not found")
        q = s.query(Comment).filter(Comment.epic_id == epic_id)
    else:
        raise InvalidValue("exactly one of task_id/story_id/epic_id must be set")
    return q.order_by(Comment.created_at, Comment.id).all()

def delete_comment(s: Session, id: int) -> bool:
    comment = s.get(Comment, id)
    if not comment:
        return False
    s.delete(comment); _commit(s); return True

# ---------- Sprint ----------
def _check_sprint_status(status: str) -> None:
    if status not in ALL_SPRINT_STATUSES:
        raise InvalidValue(f"invalid sprint status '{status}'")

def _now():
    from datetime import datetime, UTC
    return datetime.now(UTC).replace(tzinfo=None)

# ---------- Attachment ----------
import os as _os
import uuid as _uuid

ATTACHMENT_DIR = _os.getenv("AGENTBOARD_ATTACHMENT_DIR", "data/attachments")
ATTACHMENT_MAX_SIZE = int(_os.getenv("AGENTBOARD_ATTACHMENT_MAX_SIZE", str(10 * 1024 * 1024)))  # 10 MB
ATTACHMENT_ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "application/pdf",
    "text/plain", "text/markdown", "text/csv",
    "application/json", "application/xml",
    "application/zip", "application/gzip",
}

def _attachment_dir() -> str:
    _os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    return ATTACHMENT_DIR

# ---------- AgentSchedule / AgentRun ----------
import re as _re

_CRON_PATTERN = _re.compile(
    # 支持 */n 步长语法（如 */1 每分钟，*/5 每5分钟）
    r"^(\*(?:/\d+)?|[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?(?:,[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?)*)\s+"
    r"(\*(?:/\d+)?|1?\d|2[0-3])(?:-[1-2]?\d(?:/\d+)?)?(?:,(?:1?\d|#[0-3]))*\s+"
    r"(\*(?:/\d+)?|[1-2]?\d|3[01])(?:-[1-3]?\d(?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|1?\d|1[0-2])(?:-1[0-2](?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|[0-7])(?:-[0-7](?:/\d+)?)?(?:,[0-7](?:-[0-7](?:/\d+)?)?)*$"
)

def _validate_cron(expr: str) -> None:
    """校验 cron 表达式格式（5 字段：分 时 日 月 周）。"""
    if not _CRON_PATTERN.match(expr.strip()):
        raise InvalidValue(f"invalid cron expression: {expr}")

#: 可绑定到 AgentSchedule.agent 的合法 Agent 名（与 executor.KNOWN_AGENTS 对应；
#: 校验仅防手滑，松绑后实际分发由 executor 注册表决定）
SCHEDULE_AGENTS = ("codex", "claude", "workbuddy", "qoder", "minimax")

#: 任务优先级权重（值越大优先级越高；用于 pick_eligible_task 排序与门槛）
PRIORITY_RANK = {
    Priority.HIGHEST: 5, Priority.HIGH: 4, Priority.MEDIUM: 3,
    Priority.LOW: 2, Priority.LOWEST: 1,
}

#: 执行器可自动领取的任务状态（未开始、可执行的活；Story 265 后仅 todo）
ELIGIBLE_TASK_STATUSES = (Status.TODO,)

def _validate_schedule_filters(*, agent, task_priority, task_type, epic_id) -> None:
    """校验 AgentSchedule 绑定/筛选字段（None = 不设，均合法）。"""
    if agent is not None and agent not in SCHEDULE_AGENTS:
        raise InvalidValue(
            f"invalid agent '{agent}', must be one of {', '.join(SCHEDULE_AGENTS)}"
        )
    if task_priority is not None and task_priority not in ALL_PRIORITIES:
        raise InvalidValue(f"invalid task_priority '{task_priority}'")
    if task_type is not None and task_type not in ALL_TYPES:
        raise InvalidValue(f"invalid task_type '{task_type}'")

def pick_eligible_task(s: Session, schedule: AgentSchedule):
    """
    为「项目/Agent 级」schedule 挑选下一个 eligible task。

    规则：
    - 固定 ``task_id`` → 直接返回该 task（存在即返回，兼容旧单任务语义）；
    - 项目级：``status ∈ (todo,)``（Story 265 backlog 已下线，仅 todo 仍 eligible），
      按 ``epic_id`` / ``task_type`` 过滤，
      ``task_priority`` 为**最低门槛**（≥ 该优先级才 eligible），
      结果按优先级降序 + id 升序取第一个；
    - 无匹配返回 None（调用方跳过本次触发）。

    Returns:
        Task | None
    """
    if schedule.task_id is not None:
        return s.get(Task, schedule.task_id)
    q = s.query(Task).filter(
        Task.project_id == schedule.project_id,
        Task.status.in_(ELIGIBLE_TASK_STATUSES),
    )
    if schedule.epic_id is not None:
        # Task 不直接挂 epic_id，经 story 归属过滤
        q = q.filter(
            Task.story_id.in_(
                s.query(Story.id).filter(Story.epic_id == schedule.epic_id)
            )
        )
    if schedule.task_type is not None:
        q = q.filter(Task.type == schedule.task_type)
    if schedule.task_priority is not None:
        threshold = PRIORITY_RANK[schedule.task_priority]
        eligible_priorities = [
            p for p, rank in PRIORITY_RANK.items() if rank >= threshold
        ]
        q = q.filter(Task.priority.in_(eligible_priorities))
    # 优先级降序（highest 优先）+ id 升序（稳定、可预测）
    from sqlalchemy import case
    rank_case = case(
        *[(Task.priority == p, r) for p, r in PRIORITY_RANK.items()],
        else_=0,
    )
    return q.order_by(rank_case.desc(), Task.id.asc()).first()

def delete_run(s: Session, id: int) -> bool:
    run = s.get(AgentRun, id)
    if not run:
        return False
    s.delete(run); _commit(s); return True

def get_api_key(s: Session, *, user_id: int, api_key_id: int) -> ApiKey | None:
    return s.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()

def update_api_key(
    s: Session, item: ApiKey, *, name: str | None = None,
    enabled: bool | None = None, permissions: list[str] | None = None,
) -> ApiKey:
    if name is not None:
        item.name = name.strip()
    if enabled is not None:
        item.enabled = enabled
    if permissions is not None:
        item.permissions = auth.encode_permissions(permissions)
    item.updated_at = models._now()
    _commit(s)
    s.refresh(item)
    return item

def lookup_api_key_by_hash(s: Session, key_hash: str) -> ApiKey | None:
    return s.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()

def touch_api_key(s: Session, item: ApiKey) -> None:
    item.last_used_at = models._now()
    _commit(s)

# ---------- Paged response ----------
def paginated_result(items: list, total: int) -> dict:
    return {"items": items, "total": total}

# ---------- Project visibility helpers ----------
def user_is_project_member(s: Session, project_id: int, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
        is not None
    )

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

# ---------- ProjectMember ----------

# ---------- Child-resource -> project resolution (access control) ----------

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

def get_schedule_project_id(s: Session, schedule_id: int) -> int | None:
    sch = s.get(AgentSchedule, schedule_id)
    return sch.project_id if sch else None

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

def get_dependency_project_id(s: Session, dependency_id: int) -> int | None:
    d = s.get(TaskDependency, dependency_id)
    if not d:
        return None
    return get_task_project_id(s, d.task_id)

# ---------- Notification ----------

# ---------- Project statistics ----------

# ---------- Admin: user management ----------

def list_all_projects_admin(s: Session, limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    """管理员视角：所有项目（带成员数统计）"""
    q = s.query(Project).order_by(Project.id.desc())
    total = q.count()
    projects = _paginate(q, limit, offset).all()
    result = []
    for p in projects:
        row = _ser(p)
        row["member_count"] = (
            s.query(ProjectMember)
            .filter(ProjectMember.project_id == p.id)
            .count()
        ) or 0
        result.append(row)
    return result, total

# ---------- Visibility-filtered project list ----------
def list_accessible_projects(
    s: Session, user_id: int | None, limit: int | None = None, offset: int = 0,
) -> tuple[list, int]:
    """返回用户可见的项目列表。

    访问规则（2026-07-21 邀请制）：
    - 管理员：可见全部项目（``user.is_admin=True``）。
    - 普通用户：仅可见自己是成员的项目（邀请制）。
    - 未登录：空列表。

    ``abk_`` API Key 经 ``_current_user()`` 解析为关联用户的完整身份
    （含 ``is_admin``），因此权限与用户一致 —— 管理员 key 可见全部，
    普通用户 key 仅见成员项目。
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
    total = q.count()
    return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total

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

# ---------- Dashboard overview（跨项目聚合统计，首页性能优化） ----------
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

# ---------- Epic 20: 批量操作 ----------
def batch_update_task_status(s: Session, task_ids: list[int], new_status: str,
                             *, changed_by: int | None = None,
                             status_reason: str | None = None) -> dict:
    """批量更新任务状态，返回成功和失败的任务ID列表（Story 265 后校验 status_reason）。"""
    _check_status(new_status)
    new = Status(new_status)
    new_reason = _validate_status_reason(new, status_reason)
    updated = []
    errors = []
    for tid in task_ids:
        t = s.get(Task, tid)
        if not t:
            errors.append({"id": tid, "error": f"task {tid} not found"})
            continue
        current = Status(t.status)
        if current != new:
            if new == Status.BLOCKED:
                ok = True  # blocked 全向可达
            elif current == Status.BLOCKED:
                prev = t.previous_status
                ok = (prev and Status(prev) == new) or new in transitions_for(
                    _task_needs_design(s, t)).get(Status.BLOCKED, set())
            else:
                ok = new in transitions_for(_task_needs_design(s, t)).get(current, set())
            if not ok:
                errors.append({"id": tid, "error": f"illegal transition {t.status} -> {new}"})
                continue
        old_status = t.status
        if old_status != str(new):
            t.status = new
            t.status_reason = new_reason
            if new == Status.BLOCKED:
                t.previous_status = old_status
            elif old_status == Status.BLOCKED:
                t.previous_status = None
            _record_status_history(s, tid, old_status, str(new), changed_by=changed_by,
                                   reason="batch")
        updated.append(tid)
    _commit(s)
    return {"updated": updated, "errors": errors}

def batch_assign_sprint(s: Session, task_ids: list[int], sprint_id: int | None) -> dict:
    """批量分配 Sprint，支持将任务移入或移出 Sprint。"""
    updated = []
    errors = []
    sprint = None
    if sprint_id is not None:
        sprint = s.get(Sprint, sprint_id)
        if not sprint:
            raise InvalidValue(f"sprint {sprint_id} not found")
        if sprint.status == SprintStatus.COMPLETED:
            raise InvalidValue("cannot assign task to a completed sprint")
    for tid in task_ids:
        t = s.get(Task, tid)
        if not t:
            errors.append({"id": tid, "error": f"task {tid} not found"})
            continue
        if sprint and sprint.project_id != t.project_id:
            errors.append({"id": tid, "error": f"task {tid} does not belong to sprint's project"})
            continue
        t.sprint_id = sprint_id
        updated.append(tid)
    _commit(s)
    return {"updated": updated, "errors": errors}

def batch_delete_tasks(s: Session, task_ids: list[int]) -> dict:
    """批量删除任务，返回成功和失败的任务ID列表。"""
    deleted = []
    errors = []
    for tid in task_ids:
        t = s.get(Task, tid)
        if not t:
            errors.append({"id": tid, "error": f"task {tid} not found"})
            continue
        s.query(Comment).filter(Comment.task_id == tid).delete(synchronize_session=False)
        s.delete(t)
        deleted.append(tid)
    _commit(s)
    return {"deleted": deleted, "errors": errors}

# ---------- Epic 20: 增强搜索与排序 ----------
def search_tasks_enhanced(
    s: Session, *,
    project_id: int | None = None,
    epic_id: int | None = None,
    story_id: int | None = None,
    sprint_id: int | None = None,
    type: str | list[str] | None = None,
    status: str | list[str] | None = None,
    priority: str | list[str] | None = None,
    q: str | None = None,
    sort_by: str = "id",
    sort_order: str = "desc",
    limit: int | None = None,
    offset: int = 0,
):
    """增强搜索：支持多值过滤（status[], priority[]）和排序。"""
    qry = s.query(Task)
    if project_id is not None:
        qry = qry.filter(Task.project_id == project_id)
    if story_id is not None:
        qry = qry.filter(Task.story_id == story_id)
    if sprint_id is not None:
        qry = qry.filter(Task.sprint_id == sprint_id)
    if type is not None:
        if isinstance(type, list):
            qry = qry.filter(Task.type.in_(type))
        else:
            _check_type(type)
            qry = qry.filter(Task.type == type)
    if status is not None:
        if isinstance(status, list):
            for s_val in status:
                _check_status(s_val)
            qry = qry.filter(Task.status.in_(status))
        else:
            _check_status(status)
            qry = qry.filter(Task.status == status)
    if priority is not None:
        if isinstance(priority, list):
            for p_val in priority:
                _check_priority(p_val)
            qry = qry.filter(Task.priority.in_(priority))
        else:
            _check_priority(priority)
            qry = qry.filter(Task.priority == priority)
    if epic_id is not None:
        qry = qry.join(Story, Task.story_id == Story.id).filter(Story.epic_id == epic_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Task.title.ilike(like), Task.description.ilike(like),
                              Task.spec.ilike(like)))

    # 排序
    sort_col = {
        "id": Task.id, "created_at": Task.created_at, "updated_at": Task.updated_at,
        "priority": Task.priority, "status": Task.status, "title": Task.title,
    }.get(sort_by, Task.id)
    if sort_order.lower() == "asc":
        qry = qry.order_by(sort_col.asc())
    else:
        qry = qry.order_by(sort_col.desc())

    return _paginate(qry, limit, offset).all()

# ---------- Epic 20: 数据导出 ----------
def export_project_data(s: Session, project_id: int) -> dict:
    """导出项目完整数据（项目 + Epics + Stories + Tasks）。"""
    project = s.get(Project, project_id)
    if not project:
        raise NotFound(f"project {project_id} not found")

    # 获取所有 Epics
    epics = s.query(Epic).filter(Epic.project_id == project_id).all()
    epic_ids = [e.id for e in epics]

    # 获取所有 Stories
    stories = []
    story_ids = []
    if epic_ids:
        stories = s.query(Story).filter(Story.epic_id.in_(epic_ids)).all()
        story_ids = [st.id for st in stories]

    # 获取所有 Tasks
    task_filter = Task.project_id == project_id
    if story_ids:
        task_filter = or_(task_filter, Task.story_id.in_(story_ids))
    tasks = s.query(Task).filter(task_filter).all()

    return {
        "project": _ser(project),
        "epics": [_ser(e) for e in epics],
        "stories": [_ser(st) for st in stories],
        "tasks": [_ser(t) for t in tasks],
    }

def export_story_data(s: Session, story_id: int) -> dict:
    """导出 Story 及所有子任务数据。"""
    story = s.get(Story, story_id)
    if not story:
        raise NotFound(f"story {story_id} not found")

    tasks = s.query(Task).filter(Task.story_id == story_id).all()
    return {
        "story": _ser(story),
        "tasks": [_ser(t) for t in tasks],
    }

# ---------- Epic 22 Story 22.1: 审计日志 ----------
def create_audit_log(
    s: Session, *, user_id: int | None, action: str, entity_type: str,
    entity_id: int | None = None, method: str = "GET", path: str = "",
    ip_address: str | None = None, user_agent: str | None = None,
    request_body: str | None = None, response_status: int | None = None,
    duration_ms: int | None = None,
) -> AuditLog:
    """创建审计日志条目。"""
    log = AuditLog(
        user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id,
        method=method, path=path, ip_address=ip_address, user_agent=user_agent,
        request_body=request_body, response_status=response_status, duration_ms=duration_ms,
    )
    s.add(log)
    _commit(s)
    return log

def list_audit_logs(
    s: Session, *, project_id: int | None = None, entity_type: str | None = None,
    entity_id: int | None = None, user_id: int | None = None,
    action: str | None = None, limit: int | None = None, offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """查询审计日志列表。"""
    qry = s.query(AuditLog)
    if entity_type:
        qry = qry.filter(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        qry = qry.filter(AuditLog.entity_id == entity_id)
    if user_id is not None:
        qry = qry.filter(AuditLog.user_id == user_id)
    if action:
        qry = qry.filter(AuditLog.action == action)
    total = qry.count()
    qry = qry.order_by(AuditLog.created_at.desc())
    items = _paginate(qry, limit, offset).all()
    return items, total

# ---------- Epic 22 Story 22.2: 任务依赖关系 ----------
def add_task_dependency(
    s: Session, *, task_id: int, depends_on_id: int, dependency_type: str = "blocks",
) -> TaskDependency:
    """添加任务依赖关系。"""
    if task_id == depends_on_id:
        raise InvalidValue("task cannot depend on itself")
    # 检查是否已存在
    existing = s.query(TaskDependency).filter(
        TaskDependency.task_id == task_id,
        TaskDependency.depends_on_id == depends_on_id,
    ).first()
    if existing:
        raise Duplicate(f"dependency already exists")
    task = s.get(Task, task_id)
    dep_task = s.get(Task, depends_on_id)
    if not task:
        raise NotFound(f"task {task_id} not found")
    if not dep_task:
        raise NotFound(f"task {depends_on_id} not found")
    dep = TaskDependency(
        task_id=task_id, depends_on_id=depends_on_id, dependency_type=dependency_type,
    )
    s.add(dep)
    _commit(s)
    return dep

def remove_task_dependency(s: Session, dependency_id: int) -> None:
    """移除任务依赖关系。"""
    dep = s.get(TaskDependency, dependency_id)
    if not dep:
        raise NotFound(f"dependency {dependency_id} not found")
    s.delete(dep)
    _commit(s)

def get_task_dependencies(s: Session, task_id: int) -> dict:
    """获取任务的所有依赖关系。"""
    deps = s.query(TaskDependency).filter(TaskDependency.task_id == task_id).all()
    blockers = [
        {"id": d.id, "task_id": d.depends_on_id, "type": d.dependency_type,
         "task": _ser(s.get(Task, d.depends_on_id)) if s.get(Task, d.depends_on_id) else None}
        for d in deps
    ]
    # 反向依赖：该任务被谁阻塞
    blocked_by = s.query(TaskDependency).filter(TaskDependency.depends_on_id == task_id).all()
    blocking = [
        {"id": d.id, "task_id": d.task_id, "type": d.dependency_type,
         "task": _ser(s.get(Task, d.task_id)) if s.get(Task, d.task_id) else None}
        for d in blocked_by
    ]
    return {"blockers": blockers, "blocked_by": blocking}

# ---------- Epic 22 Story 22.4: Webhook 配置 ----------

# ---------- Epic 22 Story 22.3: 数据导入 ----------
def import_tasks_from_json(s: Session, project_id: int, data: dict) -> dict:
    """从 JSON 数据导入任务。

    8/17 review 修复：
    - P1 #1（首次）：默认值与 model CheckConstraint 对齐（type/status/priority
      必须落在 ALL_TYPES / ALL_STATUSES / ALL_PRIORITIES 内）；调用方传非法
      值时通过 _check_* 早失败，不再依赖 DB flush 抛 IntegrityError 兜底。
      旧"task"/"backlog"默认值与 ItemType.DEV / Status.TODO 一一映射。
    - P1 #2（本轮）：每条 item 用 ``s.begin_nested()`` 包成 SAVEPOINT——
      单条失败（校验失败 / DB IntegrityError）只回滚该 SAVEPOINT，**不影响**
      同批其它已 flush 成功但未 commit 的合法条目。**外层** transaction
      在循环结束后由 ``_commit`` 一次性提交。
      ⚠️ SAVEPOINT 不提供跨 session 并发互斥；它只解决「同 session 内
      per-item 失败不要牵连同批其它 item」这一类问题。
    """
    imported = []
    errors = []
    tasks_data = data.get("tasks", [])
    for item in tasks_data:
        try:
            # SAVEPOINT：单条 item 失败只回滚自身，不影响同批其它条目。
            with s.begin_nested():
                title = _required(item.get("title", "").strip(), "title", 300)
                task_type = item.get("type", ItemType.DEV)
                _check_type(task_type)
                task_status = item.get("status", Status.TODO)
                _check_status(task_status)
                # 8/17 review：priority 也用枚举常量默认（保持 type/status/priority
                # 三处一致；Priority 已在文件顶部 import）。
                task_priority = item.get("priority", Priority.MEDIUM)
                _check_priority(task_priority)
                task = Task(
                    project_id=project_id,
                    title=title,
                    type=task_type,
                    description=item.get("description", ""),
                    priority=task_priority,
                    status=task_status,
                )
                s.add(task)
                s.flush()
            # SAVEPOINT 提交/回滚后再 append——失败时 task 对象可能 detached。
            imported.append({"id": task.id, "title": task.title})
        except Exception as e:
            # SAVEPOINT 已自动回滚，session 状态干净，可继续下一条。
            errors.append({"title": item.get("title", "?"), "error": str(e)})
    # 外层一次性 commit；任意数量的 SAVEPOINT 全部收口后落地。
    _commit(s)
    return {"imported": imported, "errors": errors}

# ---------- Documents (Epic 15：项目文档维护 / 多成员·多 Agent 协作) ----------
def _check_document_type(value: str) -> None:
    if value not in ALL_DOCUMENT_TYPES:
        raise InvalidValue(f"invalid document type '{value}'")

def _check_document_status(value: str) -> None:
    if value not in ALL_DOCUMENT_STATUSES:
        raise InvalidValue(f"invalid document status '{value}'")

def _check_document_folder(s: Session, folder_id: int, project_id: int) -> DocumentFolder:
    """校验文件夹存在且属于指定项目；通过则返回该文件夹，否则抛 InvalidValue。"""
    f = s.get(DocumentFolder, folder_id)
    if not f:
        raise InvalidValue(f"folder {folder_id} not found")
    if f.project_id != project_id:
        raise InvalidValue("folder does not belong to the document's project")
    return f

def _check_document_links(s: Session, *, project_id: int, epic_id: int | None,
                          story_id: int | None) -> None:
    """校验文档关联的 epic/story 存在且属于同一项目（目录结构一致性）。

    - epic_id：必须存在且 ``epic.project_id == project_id``；
    - story_id：必须存在且其所属 epic 属于该项目；若同时指定 epic_id，
      story 必须属于该 epic（防止文档的 story/epic 关联错位导致目录结构混乱）。
    """
    if epic_id is not None:
        e = s.get(Epic, epic_id)
        if not e:
            raise InvalidValue(f"epic {epic_id} not found")
        if e.project_id != project_id:
            raise InvalidValue(f"epic {epic_id} 不属于项目 {project_id}")
    if story_id is not None:
        st = s.get(Story, story_id)
        if not st:
            raise InvalidValue(f"story {story_id} not found")
        st_epic = s.get(Epic, st.epic_id)
        if not st_epic or st_epic.project_id != project_id:
            raise InvalidValue(f"story {story_id} 不属于项目 {project_id}")
        if epic_id is not None and st.epic_id != epic_id:
            raise InvalidValue(f"story {story_id} 不属于 epic {epic_id}")

def _folder_is_descendant(s: Session, folder_id: int, ancestor_id: int) -> bool:
    """ancestor_id 是否为 folder_id 的祖先（含自身）？用于移动文件夹时防环。"""
    cur: int | None = ancestor_id
    seen: set[int] = set()
    while cur is not None:
        if cur == folder_id:
            return True
        if cur in seen:
            return False
        seen.add(cur)
        f = s.get(DocumentFolder, cur)
        cur = f.parent_id if f else None
    return False

_DOCUMENT_SORT_WHITELIST = {"updated", "created", "title"}

# ---------------------------------------------------------------------------
# Epic 139：DocumentRevision（不可变快照）+ 乐观锁
# ---------------------------------------------------------------------------

def _next_revision_number(s: Session, document_id: int) -> int:
    """取当前最大 revision_number + 1；空表时返回 1。"""
    last = (
        s.query(func.max(DocumentRevision.revision_number))
        .filter(DocumentRevision.document_id == document_id)
        .scalar()
    )
    return (last or 0) + 1

def create_revision(
    s: Session, *, document_id: int, title: str, content: str,
    change_note: str, author_id: int | None = None, author: str | None = None,
    is_restore: bool = False, restored_from_revision: int | None = None,
) -> DocumentRevision:
    """在事务中追加一条不可变 revision；调用方负责 _commit。文档不存在抛 NotFound。

    不会触碰 Document 头；如需同步 current_revision_id / current_revision_number，
    请走 save_document_with_revision()。
    """
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    change_note = (change_note or "").strip()[:500]
    rev = DocumentRevision(
        document_id=document_id,
        revision_number=_next_revision_number(s, document_id),
        title=_required(title, "title", 300),
        content=content or "",
        author_id=author_id, author=author,
        change_note=change_note,
        is_restore=is_restore, restored_from_revision=restored_from_revision,
    )
    s.add(rev); _commit(s); s.refresh(rev); return rev

def list_revisions(
    s: Session, document_id: int, *, limit: int | None = None, offset: int = 0,
):
    """按 revision_number 倒序列出；含 current_revision_number 头指针信息。"""
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    qry = (
        s.query(DocumentRevision)
        .filter(DocumentRevision.document_id == document_id)
        .order_by(DocumentRevision.revision_number.desc())
    )
    return _paginate(qry, limit, offset).all()

def get_revision(s: Session, document_id: int, revision_number: int) -> DocumentRevision:
    """取指定 revision；不存在抛 NotFound。"""
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    rev = (
        s.query(DocumentRevision)
        .filter(
            DocumentRevision.document_id == document_id,
            DocumentRevision.revision_number == revision_number,
        )
        .first()
    )
    if not rev:
        raise NotFound(f"document {document_id} revision {revision_number} not found")
    return rev

def restore_revision(
    s: Session, *, id: int, revision_number: int,
    change_note: str, author_id: int | None = None, author: str | None = None,
) -> Document:
    """把旧版 content 复制为新 revision（不修改历史）。返回更新后的 Document。

    - 新 revision_number = max + 1；change_note 必填并自动加前缀「回滚自 r{N}」；
    - current_revision_id / current_revision_number 指向新 revision；
    - 旧 revision 保持不变。
    """
    d = s.get(Document, id)
    if not d:
        raise NotFound(f"document {id} not found")
    src = get_revision(s, id, revision_number)
    note = (change_note or "").strip()[:500]
    if not note:
        raise InvalidValue("change_note is required for restore")
    new_rev = DocumentRevision(
        document_id=id,
        revision_number=_next_revision_number(s, id),
        title=src.title,
        content=src.content,
        author_id=author_id, author=author,
        change_note=f"回滚自 r{revision_number}：{note}",
        is_restore=True, restored_from_revision=revision_number,
    )
    s.add(new_rev); s.flush()
    d.title = src.title
    d.content = src.content
    d.current_revision_id = new_rev.id
    d.current_revision_number = new_rev.revision_number
    _commit(s); s.refresh(d); s.refresh(new_rev)
    return d

# 旧 update_document 兼容：头部元数据（type/status/folder/epic/story）仍走原路径；
# 标题/正文变更请改走 save_document_with_revision（带 expected_revision_number）。
# 为避免破坏既有调用，update_document 继续接受 title/content 字段（无乐观锁），
# 兼容路径不再创建 revision，仅更新 Document 头。Epic 139 后续会引导 UI 切到新接口。

# ---------- Proposals (Epic 96 P0：Proposal 澄清回路 / 人机协同需求分析) ----------
def _check_proposal_status(value: str) -> None:
    if value not in ALL_PROPOSAL_STATUSES:
        raise InvalidValue(f"invalid proposal status '{value}'")

def _proposal_or_404(s: Session, proposal_id: int) -> Proposal:
    p = s.get(Proposal, proposal_id)
    if not p:
        raise NotFound(f"proposal {proposal_id} not found")
    return p

def update_proposal(s: Session, id: int, **fields) -> Proposal | None:
    """编辑提案正文（状态流转请用 set_proposal_status）。

    用户编辑 title/content 时，若提案处于澄清流（queued/analyzing/awaiting/
    answered/converged），**回退 pending**（待开始）——编辑后需重新点击
    「开始 grill」才重新入队；已答历史保留（全量重放不丢上下文）。
    worker 写入 converged_spec / 回填 story_id 等**非用户编辑**字段不回退。
    """
    p = s.get(Proposal, id)
    if not p:
        return None
    allowed = {"title", "content", "converged_spec", "story_id"}
    edited_user_fields = False
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "story_id" and not s.get(Story, v):
            raise NotFound(f"story {v} not found")
        if k in ("title", "content"):
            edited_user_fields = True
        setattr(p, k, v)
    # 编辑回退：澄清流状态 → pending（清租约，等「开始 grill」重新入队）
    # 2026-08-09 review 修复：ticket_preparing（生成中）编辑同样回退，
    # 并把该提案未完成的转换请求置 failed——防止 agent 用并发修改后的
    # 内容生成 ticket。
    if edited_user_fields and p.status in (
        ProposalStatus.QUEUED.value, ProposalStatus.ANALYZING.value,
        ProposalStatus.AWAITING.value, ProposalStatus.ANSWERED.value,
        ProposalStatus.CONVERGED.value, ProposalStatus.TICKET_PREPARING.value,
    ):
        was_ticket_preparing = p.status == ProposalStatus.TICKET_PREPARING.value
        p.status = ProposalStatus.PENDING.value
        if was_ticket_preparing:
            _cancel_open_ticket_requests(s, id, reason="提案被编辑，生成已取消")
        p.claimed_by = ""
        p.claimed_at = None
        p.claimed_by = ""
        p.claimed_at = None
    _commit(s); s.refresh(p); return p

def delete_proposal(s: Session, id: int) -> bool:
    p = s.get(Proposal, id)
    if not p:
        return False
    # 显式清理子表（外键 ondelete=CASCADE 也会兜底；SQLite 默认不强制外键）
    s.query(ProposalQuestion).filter(ProposalQuestion.proposal_id == id).delete(
        synchronize_session=False,
    )
    s.query(ProposalRound).filter(ProposalRound.proposal_id == id).delete(
        synchronize_session=False,
    )
    s.delete(p); _commit(s); return True

# ---- Step 3：状态迁移副作用注册（委托 StateMachine 前的业务行为） ----

def _sm_failed_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """FAILED：写 error（保留原语义：error 参数优先，其次既有值，兜底固定文案）。"""
    error = ctx.get("error")
    p.error = error or p.error or "unspecified failure"

def _sm_clear_error_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """非 FAILED 且未显式传 error：清空历史错误。"""
    if ctx.get("error") is None:
        p.error = ""

def _sm_success_clear_retry(s: Session, p: Proposal, ctx: dict) -> None:
    """成功终态（收敛/生成工单）清零自动重投计数：agent 已恢复或人工接管。"""
    p.auto_retry_count = 0

def _sm_claim_lease_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """进入 analyzing：盖上租约时间戳（含旧版 PUT /status 认领路径）。"""
    p.claimed_at = utc_now()

def _sm_clear_lease_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """离开 analyzing：清空租约，防止已收敛/失败的提案仍挂着持有者。"""
    p.claimed_by = ""
    p.claimed_at = None

def _sm_apply_side_effects(s: Session, p: Proposal, ctx: dict) -> None:
    """统一副作用分派：按目标状态执行对应注册副作用。"""
    new = ProposalStatus(p.status)  # StateMachine.execute 已推进 status
    if new is ProposalStatus.FAILED:
        _sm_failed_effect(s, p, ctx)
    elif ctx.get("error") is None:
        _sm_clear_error_effect(s, p, ctx)
    if new in _SUCCESS_TERMINALS and (p.auto_retry_count or 0) > 0:
        _sm_success_clear_retry(s, p, ctx)
    if new is ProposalStatus.ANALYZING:
        _sm_claim_lease_effect(s, p, ctx)
    else:
        _sm_clear_lease_effect(s, p, ctx)

# 注册到 StateMachine（所有迁移共享统一副作用，按目标状态分派）
bind_side_effects({
    st: TransitionSpec(side_effects=(_sm_apply_side_effects,))
    for st in ALL_PROPOSAL_STATUSES
})

_PROPOSAL_SM = ProposalStateMachine()

# Worker 认领租约默认时长（秒）；与 worker.py AGENTBOARD_WORKER_LEASE 默认值一致。
DEFAULT_CLAIM_LEASE_SECONDS = 1800

def reclaim_stale_proposals(
    s: Session, *, lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
) -> list[int]:
    """把租约过期的 analyzing 提案批量回退 queued，返回被回收的 id 列表。

    这是整个自动化闭环唯一的丢单兜底：持有者进程被 kill 后，提案必须能重新入队。

    判定依据是 ``claimed_at`` 而非 ``updated_at``：后者带 onupdate，用户作答、
    PATCH converged_spec 等**与持有者无关**的写入都会刷新它，导致一个早已崩溃的
    Worker 的租约被旁人不断续期，提案永久卡死在 analyzing。
    """
    if lease_seconds < 0:
        raise InvalidValue("lease_seconds must be >= 0")
    now = utc_now()
    cutoff = now - timedelta(seconds=lease_seconds)
    analyzing = ProposalStatus.ANALYZING.value
    # claimed_at 为 NULL 的 analyzing 行只可能来自本迁移之前（历史遗留），
    # 对它们退回用 updated_at 兜底，避免升级后这批行永远无法回收。
    stale = or_(
        Proposal.claimed_at < cutoff,
        and_(Proposal.claimed_at.is_(None), Proposal.updated_at < cutoff),
    )
    ids = [
        row[0]
        for row in s.query(Proposal.id)
        .filter(Proposal.status == analyzing, stale)
        .all()
    ]
    if not ids:
        return []
    s.execute(
        update(Proposal)
        .where(Proposal.id.in_(ids), Proposal.status == analyzing)
        .values(
            status=ProposalStatus.QUEUED.value,
            claimed_by="",
            claimed_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    _commit(s)
    s.expire_all()
    return ids

# Agent 不可用类错误关键词：worker 拉起 CLI agent 失败（命令无法启动/找不到/
# 调用异常等）。这类失败**不应要求前端手动重试**——由后端 job（recover_failed）
# 自动回退 queued 重投，直至 agent 恢复或达到次数上限转人工。
AGENT_ERROR_KEYWORDS = (
    "Agent 命令无法启动", "Agent 调用失败", "Agent 调用异常",
    "无法启动", "Invocation", "找不到",
)

# 进入这些状态视为提案成功（或人工接管），清零自动重投计数
_SUCCESS_TERMINALS = {
    ProposalStatus.CONVERGED, ProposalStatus.STORY_CREATED,
    ProposalStatus.TICKET_CREATED,
}

def recover_failed_proposals(
    s: Session, *, window_seconds: int = 120, max_retries: int = 5,
) -> list[int]:
    """把「Agent 不可用」导致的 failed 提案自动回退 queued 重投（后端 job）。

    与 ``reclaim_stale_proposals``（analyzing 租约超时）互补：
    - reclaim：worker 崩溃后卡在 analyzing → 回退 queued；
    - recover：agent CLI 不可用导致的 failed → 自动重试，前端不做手动 retry。

    规则：
    - 仅处理 error 匹配 AGENT_ERROR_KEYWORDS 的 failed 提案（人工判定失败如
      轮次上限超限 / 用户中止**不**自动重投）；
    - 重投计数用 ``auto_retry_count`` 字段（worker 每次失败会覆盖 error 文本，
      不能编码进 error）；达到 max_retries 停投转人工，避免 agent 永久不可用时
      无限循环；
    - 距上次失败（updated_at）不足 window_seconds 跳过，控制重投频率。
    """
    if window_seconds < 0:
        raise InvalidValue("window_seconds must be >= 0")
    now = utc_now()
    cutoff = now - timedelta(seconds=window_seconds)
    failed_rows = (
        s.query(Proposal)
        .filter(Proposal.status == ProposalStatus.FAILED.value)
        .all()
    )
    recovered: list[int] = []
    for p in failed_rows:
        err = p.error or ""
        if not any(k in err for k in AGENT_ERROR_KEYWORDS):
            continue
        if (p.auto_retry_count or 0) >= max_retries:
            continue
        if p.updated_at is not None and p.updated_at > cutoff:
            continue
        p.status = ProposalStatus.QUEUED.value
        p.claimed_by = ""
        p.claimed_at = None
        p.auto_retry_count = (p.auto_retry_count or 0) + 1
        p.updated_at = now
        recovered.append(p.id)
    if recovered:
        _commit(s)
    return recovered

def answer_proposal_question(
    s: Session, question_id: int, *, answer: str = "", unsure: bool = False,
    user_id: int | None = None,
) -> ProposalQuestion:
    """用户作答单条问题；``unsure=True`` 表示标记不确定（视为已处理）。"""
    qs = s.get(ProposalQuestion, question_id)
    if not qs:
        raise NotFound(f"proposal question {question_id} not found")
    answer = (answer or "").strip()
    if not answer and not unsure:
        raise InvalidValue("answer is required unless marked unsure")
    if user_id is not None and not s.get(User, user_id):
        raise InvalidValue(f"user {user_id} not found")
    qs.answer = answer
    qs.unsure = bool(unsure)
    qs.answered_at = utc_now()
    qs.answered_by = user_id
    _commit(s); s.refresh(qs)
    _maybe_mark_answered(s, qs.proposal_id)
    return qs

def _maybe_mark_answered(s: Session, proposal_id: int) -> None:
    """当前轮次问题全部处理完毕时，自动把 awaiting 推进到 answered。"""
    p = s.get(Proposal, proposal_id)
    if not p or ProposalStatus(p.status) is not ProposalStatus.AWAITING:
        return
    r = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id,
                ProposalRound.round_no == p.current_round)
        .first()
    )
    if not r:
        return
    pending = (
        s.query(ProposalQuestion)
        .filter(ProposalQuestion.round_id == r.id,
                ProposalQuestion.answered_at.is_(None))
        .count()
    )
    if pending == 0:
        p.status = ProposalStatus.ANSWERED.value
        _commit(s)

def list_proposal_rounds(s: Session, proposal_id: int) -> list[dict]:
    """按轮次正序返回澄清历史（含每轮问题），供前端问答工作台渲染。"""
    _proposal_or_404(s, proposal_id)
    rounds = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id)
        .order_by(ProposalRound.round_no.asc())
        .all()
    )
    out = []
    for r in rounds:
        qs = (
            s.query(ProposalQuestion)
            .filter(ProposalQuestion.round_id == r.id)
            .order_by(ProposalQuestion.seq.asc(), ProposalQuestion.id.asc())
            .all()
        )
        item = _ser(r)
        item["questions"] = [_ser(x) for x in qs]
        out.append(item)
    return out

# P3：converged_spec 中生成子 Task 的清单项前缀（与 generate_tasks_from_spec 一致）
_SPEC_TASK_RE = re.compile(r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)")

def convert_proposal_to_story(
    s: Session, proposal_id: int, *, epic_id: int, title: str | None = None,
) -> tuple[Story, list[Task], Proposal]:
    """人工终审确认后，把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    - 要求提案状态为 converged，且 converged_spec 非空（否则 400/422 拒绝）；
    - 要求目标 Epic 存在且属于提案所在项目；
    - Story 标题 = 显式 title 或提案标题，description = converged_spec 原文；
    - 解析 converged_spec 中的 ``- [ ]`` 清单项生成子 Task
      （同 project/story，type=dev / status=todo / priority=medium，
      取自 Task model 默认值；8/17 review P1 注释清理）；
    - 回填 proposal.story_id 并推进 converged → story_created；
    - **幂等防重放**：story_id 已回填且 Story 仍存在时直接返回既有结果，
      不重复创建（呼应 P1 全量重放 / P2 at-least-once 的既有兜底策略）。

    返回 ``(story, tasks, proposal)``。
    """
    p = _proposal_or_404(s, proposal_id)

    # 幂等：已转化过且 Story 还在 → 直接复用，避免重放产生重复 Story。
    if p.story_id is not None:
        existing = s.get(Story, p.story_id)
        if existing is not None:
            tasks = (
                s.query(Task).filter(Task.story_id == existing.id).all()
            )
            return existing, tasks, p

    if ProposalStatus(p.status) is not ProposalStatus.CONVERGED:
        raise InvalidValue(
            f"proposal {proposal_id} 当前状态为 {p.status}，仅 converged 可转化为 Story",
        )
    if not (p.converged_spec or "").strip():
        raise InvalidValue(
            f"proposal {proposal_id} 的 converged_spec 为空，无法生成 Story",
        )

    epic = s.get(Epic, epic_id)
    if epic is None:
        raise NotFound(f"epic {epic_id} not found")
    if epic.project_id != p.project_id:
        raise InvalidValue(
            f"epic {epic_id} 不属于提案所在项目 {p.project_id}",
        )

    story = create_story(
        s, epic_id=epic_id,
        title=_required(title or p.title, "title", 300),
        description=p.converged_spec,
    )
    created: list[Task] = []
    seen: set[str] = set()
    for line in (p.converged_spec or "").splitlines():
        m = _SPEC_TASK_RE.match(line)
        if not m:
            continue
        t_title = m.group(1).strip()
        if not t_title or t_title in seen:
            continue
        seen.add(t_title)
        created.append(
            create_task(
                s, project_id=p.project_id, story_id=story.id,
                title=t_title[:300], description=t_title,
                priority=Priority.MEDIUM,
            )
        )

    p.story_id = story.id
    # converged → story_created（终态）；直接改状态字段，不经 set_proposal_status
    # 的租约维护逻辑（这里不涉及 analyzing，无租约可清理）。
    p.status = ProposalStatus.STORY_CREATED.value
    p.error = ""
    _commit(s)
    s.refresh(story)
    s.refresh(p)
    for t in created:
        s.refresh(t)
    return story, created, p

# ============ Proposal → Ticket 异步转化（2026-08-08 文档 #59）============
# 转换请求状态机：pending → processing → done / failed（failed 可重置 pending 重试）。
# proposal 联动：converged → ticket_preparing → ticket_created（失败回退 converged）。

def _check_ticket_type(value: str) -> None:
    if value not in TICKET_TYPES:
        raise InvalidValue(
            f"invalid ticket type '{value}'，仅允许 {sorted(TICKET_TYPES)}",
        )

def _check_ticket_request_status(value: str) -> None:
    if value not in TICKET_REQUEST_STATUSES:
        raise InvalidValue(
            f"invalid ticket request status '{value}'，"
            f"仅允许 {sorted(TICKET_REQUEST_STATUSES)}",
        )

def _ticket_request_or_404(s: Session, request_id: int) -> ProposalTicketRequest:
    r = s.get(ProposalTicketRequest, request_id)
    if not r:
        raise NotFound(f"ticket request {request_id} not found")
    return r

def _cancel_open_ticket_requests(
    s: Session, proposal_id: int, *, reason: str,
) -> None:
    """提案被编辑回退时，取消其未完成的转换请求（pending/processing → failed）。

    2026-08-09 review 修复（中）：防止 agent 用并发修改后的内容生成 ticket。
    """
    for req in (
        s.query(ProposalTicketRequest)
        .filter(
            ProposalTicketRequest.proposal_id == proposal_id,
            ProposalTicketRequest.status.in_(
                (TICKET_REQUEST_PENDING, TICKET_REQUEST_PROCESSING),
            ),
        )
        .all()
    ):
        req.status = TICKET_REQUEST_FAILED
        req.error = (reason or "cancelled")[:2000]
        req.updated_at = utc_now()

def _validate_ticket_parents(
    s: Session, proposal: Proposal, *, type: str, epic_id: int | None,
    story_id: int | None,
) -> None:
    """层级校验：epic∈项目；story∈epic（且∈项目）；task/bug 必挂 story。"""
    if type == "epic":
        return  # epic 独立，无父级
    if not epic_id:
        raise InvalidValue(f"ticket type '{type}' 需要 epic_id")
    epic = s.get(Epic, epic_id)
    if epic is None:
        raise NotFound(f"epic {epic_id} not found")
    if epic.project_id != proposal.project_id:
        raise InvalidValue(
            f"epic {epic_id} 不属于提案所在项目 {proposal.project_id}",
        )
    if type == "story":
        return
    # task / bug：必挂 story，且 story 属于指定 epic
    if not story_id:
        raise InvalidValue(f"ticket type '{type}' 需要 story_id")
    story = s.get(Story, story_id)
    if story is None:
        raise NotFound(f"story {story_id} not found")
    if story.epic_id != epic_id:
        raise InvalidValue(
            f"story {story_id} 不属于 epic {epic_id}",
        )

def _ticket_request_by_type(
    s: Session, proposal_id: int, type: str,
) -> ProposalTicketRequest | None:
    return (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.proposal_id == proposal_id,
                ProposalTicketRequest.type == type)
        .first()
    )

def claim_ticket_request(
    s: Session, request_id: int, *, agent: str = "",
) -> ProposalTicketRequest | None:
    """**原子**认领转换请求：pending → processing（worker 竞争消费）。

    条件 UPDATE 由数据库仲裁，恰一个赢家；返回 None 表示竞争失败（已被他人
    认领 / 已完成 / 不存在），调用方据此跳过或 409。
    """
    now = utc_now()
    res = s.execute(
        update(ProposalTicketRequest)
        .where(
            ProposalTicketRequest.id == request_id,
            ProposalTicketRequest.status == TICKET_REQUEST_PENDING,
        )
        .values(
            status=TICKET_REQUEST_PROCESSING,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if res.rowcount == 1:
        _commit(s)
        req = s.get(ProposalTicketRequest, request_id)
        s.refresh(req)
        return req
    s.rollback()
    return None

def _ticket_execute_result(
    s: Session, req: ProposalTicketRequest, proposal_id: int,
) -> dict:
    """组装 execute 返回（ticket 实体序列化 + 请求）。"""
    ticket: dict | None = None
    if req.ticket_id is not None:
        if req.type == "epic":
            ticket = _ser(s.get(Epic, req.ticket_id)) if s.get(Epic, req.ticket_id) else None
        elif req.type == "story":
            ticket = _ser(s.get(Story, req.ticket_id)) if s.get(Story, req.ticket_id) else None
        else:
            ticket = _ser(s.get(Task, req.ticket_id)) if s.get(Task, req.ticket_id) else None
    return {
        "proposal": _ser(s.get(Proposal, proposal_id)),
        "request": _ser(req),
        "ticket": ticket,
    }

def fail_ticket_request(
    s: Session, request_id: int, *, error: str,
) -> ProposalTicketRequest | None:
    """标记转换请求失败：status → failed，proposal ticket_preparing → converged
    （回退，可重新点击生成）。"""
    req = _ticket_request_or_404(s, request_id)
    if req.status == TICKET_REQUEST_DONE:
        return req  # 已完成不允许改判失败
    req.status = TICKET_REQUEST_FAILED
    req.error = (error or "unspecified failure")[:2000]
    req.updated_at = utc_now()
    p = s.get(Proposal, req.proposal_id)
    if p and ProposalStatus(p.status) is ProposalStatus.TICKET_PREPARING:
        p.status = ProposalStatus.CONVERGED.value
        p.error = ""
        _commit(s)
    else:
        _commit(s)
    s.refresh(req)
    return req

def list_ticket_requests(s: Session, proposal_id: int) -> list[ProposalTicketRequest]:
    """列出提案的全部转换请求（前端轮询生成状态）。"""
    _proposal_or_404(s, proposal_id)
    return (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.proposal_id == proposal_id)
        .order_by(ProposalTicketRequest.id.asc())
        .all()
    )

def list_pending_ticket_requests(s: Session, limit: int = 20):
    """Worker 拉取待认领转换请求（status=pending）。"""
    limit = max(1, min(int(limit or 20), 200))
    return (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.status == TICKET_REQUEST_PENDING)
        .order_by(ProposalTicketRequest.id.asc())
        .limit(limit)
        .all()
    )

def get_ticket_request(s: Session, request_id: int) -> ProposalTicketRequest | None:
    """按 id 取转换请求（供端点做归属校验）。"""
    return s.get(ProposalTicketRequest, request_id)

def get_ticket_request_project_id(s: Session, request_id: int) -> int | None:
    """按请求反查项目（供项目访问中间件用）。"""
    req = s.get(ProposalTicketRequest, request_id)
    if not req:
        return None
    p = s.get(Proposal, req.proposal_id)
    return p.project_id if p else None

# ---------------------------------------------------------------------------
# Phase 4 拆分:identity/auth 函数重绑到 features.identity.service(新实现)
# 老 import 路径 `from . import service; service.register_user(...)` 走新模块。
# ---------------------------------------------------------------------------
from ...features.identity.service import (  # noqa: F401,F403
    register_user, authenticate_user, get_user, get_user_by_username,
    update_user_profile, change_user_password,
    create_api_key, list_api_keys, revoke_api_key, toggle_api_key,
    list_users, set_user_admin, get_user_by_id, has_users,
    # 末尾补:update_api_key 之前在 facade 里有重复实现,显式 re-bind 让
    # 老 `service.update_api_key` 走 features/identity/service 新版。
    update_api_key,
)

# ---------------------------------------------------------------------------
# Phase 4 第二段:work_items service 重绑(set_status 用上 TaskStateMachine)
# ---------------------------------------------------------------------------
from ...features.work_items.service import (  # noqa: F401,F403
    create_task, get_task, list_tasks, query_task_count,
    get_task_readiness, get_unlocked_dependent_tasks,
    list_task_status_history, set_status,
    try_assign_task, claim_development_task, finalize_task_assignment,
    apply_for_task, arbitrate_task,
    submit_task_for_review,
    # 末尾补:以下函数原来在 facade 里有重复实现,此处显式 re-bind 让老
    # `service.batch_update_task_status` / `service.export_project_data` 调
    # 用方自动走 features/*/service 新版(后者可能带 status_reason 校验、
    # 状态机收紧、retry 清理等后续增强)。
    batch_update_task_status,
    export_project_data,
    # T1.5：收敛 generate_tasks_from_spec 的分叉副本（core 版不继承 created_by_*），
    # 统一走 features 真源。放在末尾以确保覆盖上面已删除的本地定义。
    generate_tasks_from_spec,
)

# ---------------------------------------------------------------------------
# Phase 4 第三段:projects service 重绑(Project / Epic / Story / Sprint / Member / Stats)
# ---------------------------------------------------------------------------
from ...features.projects.service import (  # noqa: F401,F403
    create_project, get_project, list_projects, update_project, delete_project,
    create_epic, get_epic, list_epics, update_epic, delete_epic,
    create_story, get_story, list_stories, update_story, delete_story,
    create_sprint, get_sprint, list_sprints, update_sprint, delete_sprint,
    activate_sprint, complete_sprint, get_sprint_burndown,
    add_project_member, list_project_members, remove_project_member,
    update_project_member_role, get_project_member,
    # T2.1 读门：文档/task/story 列表统一从这里取「可读项目集合」，
    # 不要再各自内联一份 member_pids 查询（会漂移）。
    user_can_read_project, readable_project_ids,
    # T2.0：owner 选取规则。T2.2「移除成员 → 移交 project owner」的接收方解析
    # 必须经它，不能各自再写一份「谁是 owner」的判断。
    project_owners, resolve_project_owner,
    get_epic_project_id, get_story_project_id, get_sprint_project_id,
    get_run_project_id,
    get_project_stats,
    # Story 137：项目中心
    archive_project, unarchive_project, bulk_archive, bulk_unarchive,
    list_accessible_projects_center,
    # Story 137：默认隐藏已归档（修复 /api/projects 文档与实现分裂）。
    # 原 facade list_accessible_projects 不带 include_archived 参数，
    # 此处 re-bind 后 router 调用 ``service.list_accessible_projects(...,
    # include_archived=...)`` 走 features 版，避免 TypeError。
    list_accessible_projects,
)

# ---------------------------------------------------------------------------
# Phase 4 第四段:proposals service 重绑(Proposal / Round / Question / TicketRequest)
# ---------------------------------------------------------------------------
from ...features.proposals.service import (  # noqa: F401,F403
    create_proposal, get_proposal, list_proposals, set_proposal_status,
    claim_proposal, create_proposal_round, add_proposal_questions,
    update_proposal, list_proposal_rounds, answer_proposal_question,
    get_proposal_project_id, build_proposal_task_graph, convert_proposal_to_story,
    create_ticket_request, execute_ticket_request, reclaim_stale_ticket_requests,
    claim_ticket_request, fail_ticket_request, list_ticket_requests, list_pending_ticket_requests,
    get_ticket_request,
)

# ---------------------------------------------------------------------------
# Phase 4 第五段:documents + notifications + webhooks + scheduling service 重绑
# ---------------------------------------------------------------------------
from ...features.documents.service import (  # noqa: F401,F403
    _check_document_type, _check_document_status, _check_document_folder,
    _check_document_links, _attachment_dir,
    create_document_folder, list_document_folders, update_document_folder,
    delete_document_folder, get_document_folder_project_id,
    create_document, get_document, list_documents, count_document_comments,
    save_document_with_revision, update_document, delete_document, set_document_status,
    create_document_comment, list_document_comments, update_document_comment,
    delete_document_comment, get_document_project_id, get_document_comment_project_id,
    create_attachment, get_attachment, get_attachment_path, list_attachments,
    delete_attachment, get_attachment_project_id,
    # 末尾补:list_revisions 之前在 facade 里有重复实现,显式 re-bind 让
    # 老 `service.list_revisions` 走 features/documents/service 新版(后者
    # 带 current_revision_number 头指针信息)。
    list_revisions,
)
from ...features.notifications.service import (  # noqa: F401,F403
    create_notification, list_notifications, search_notifications,
    mark_notification_read, mark_all_notifications_read, delete_notification,
)
from ...features.webhooks.service import (  # noqa: F401,F403
    create_webhook, list_webhooks, delete_webhook, toggle_webhook,
    get_webhook_project_id, fire_webhook, fire_webhooks_for_event,
)
from ...features.scheduling.service import (  # noqa: F401,F403
    create_schedule, list_schedules, get_schedule, update_schedule, delete_schedule,
    create_run, list_runs, create_run_event, list_run_events, claim_lease, release_lease, get_run, update_run, report_run_result,
    register_agent, update_agent, agent_heartbeat, agent_deregister, list_agents,
    # Worker + AgentInstance（2026-08-26 P1：多 Worker 部署隔离）
    register_worker, list_workers, get_worker_by_id,
    upsert_agent_instance, get_agent_instance, list_agent_instances,
    delete_agent_instance, instance_heartbeat, instance_deregister,
    claim_story, submit_task_for_review,
    assign_task_reviewer, review_story, review_task,
    scan_review_timeouts, complete_story, complete_sprint,
    reclaim_stale_stories, reclaim_stale_tasks,
    # 末尾补:assign_reviewer 之前在 facade 里有重复实现,显式 re-bind 让
    # 老 `service.assign_reviewer` 走 features/scheduling/service 新版。
    assign_reviewer,
)

# ---------------------------------------------------------------------------
# 评审真源统一（Implementation Plan T0.1a/T0.1b，2026-09-02）
#
# core 此前保留了一整簇评审私有函数的**重复定义**，且与 features 版分叉；
# 可达性分析 + 运行时验证确认：公开入口（review_task / assign_task_reviewer /
# scan_review_timeouts / assign_reviewer / submit_task_for_review）全部已
# re-export 自 features，core 侧那一簇是**死代码**，features 版才是唯一真源。
#
# 因此这里反向收敛：core 删除本地死定义，统一转发到 features。
# 保留转发（而非直接删除符号）是为了不破坏 `service.X` 的既有引用与
# getattr 动态调用。
# ---------------------------------------------------------------------------
from ...features.scheduling.service import (  # noqa: F401,F403
    REVIEW_MODE_SINGLE, REVIEW_MODE_MAJORITY, DEFAULT_REVIEW_QUORUM,
    MAX_REVIEW_ROUNDS,
    get_review_mode, get_review_quorum,
    _is_reviewer_candidate, _upsert_review_vote, _review_vote_counts,
    _clear_review_votes, _settle_majority_approved, _settle_majority_rejected,
    _vote_majority, _online_reviewer_candidates,
    _reassign_story_reviewer, _reassign_task_reviewer,
)

# ---------------------------------------------------------------------------
# T1.5 统一执行门：真源在 features/work_items/ownership.py。
# 与上面同一套理由 —— 收敛成一处，别再散出第二份判据。
# ---------------------------------------------------------------------------
from ...features.work_items.ownership import (  # noqa: F401,F403
    CODE_EXCLUDED, CODE_NO_OWNER, CODE_NOT_OWNER, CODE_OK,
    GateDecision, agent_can_handle_work_item,
    assert_agent_can_handle_work_item, work_item_owner_user_id,
)

# ----------------------------------------------------------------------
# 末尾 re-bind:RevisionConflict 统一指向 features.documents.service.RevisionConflict。
# 上面 line 2199 那段早期 stub 已经在拆分前删掉;这里强制覆盖,
# 让外部 `except service.RevisionConflict` 能抓到新版异常(否则会抓到旧 class
# 对象,导致 30+ test/api 路由 except 子句不匹配)。
# ----------------------------------------------------------------------
from ...features.documents.service import RevisionConflict as _FeaturesRevisionConflict  # noqa: E402
RevisionConflict = _FeaturesRevisionConflict  # type: ignore[misc,assignment]
del _FeaturesRevisionConflict
