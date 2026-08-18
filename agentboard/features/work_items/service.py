"""WorkItems service:Task / Comment / Attachment / Dependency。

Phase 4 第二段:从 service.py 拆出。set_status 用 Phase 3 的 TaskStateMachine
统一驱动,校验/副作用/历史/缓存失效自动跑。

老 import 路径兼容:service.py 末尾重绑 set_status / create_task / get_task /
list_tasks / claim_development_task / submit_task_for_review 等到本模块。
"""
from __future__ import annotations

import json
import logging
import os as _os
import re
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容
from ...core.exceptions import Conflict, Duplicate, InvalidValue, NotFound
from .models import ATTACHMENT_DIR, Comment, Task, TaskDependency, TaskStatusHistory
from .state_machine import execute_transition

log = logging.getLogger("agentboard.features.work_items.service")

from ...core.common.enums import (
    ItemType, Priority, SprintStatus, Status, StatusReason,
    STATUS_REASONS_BY_STATUS,
)
from ...core.common.models import utc_now  # noqa: F401
from ..scheduling.models import (  # noqa: F401  (跨域常量)
    DEFAULT_REVIEW_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_SCAN_BATCH,
    TaskAssignment,
    TaskApplication,
)

from ...core.service_helpers import (
    _check_priority, _check_status, _check_type, _commit,
    _invalidate_project_stats_cache, _paginate, _parse_due_date, _required,
    _ser,
)
from ..projects.models import Agent, Epic, Project, Sprint, Story
from ..scheduling.matching import (
    normalize_assignment_mode,
    normalize_complexity,
    normalize_domain_tags,
    normalize_required_capabilities,
    score_agent_for_task,
)


# ---- 内部 helper ---------------------------------------------------------


def _record_status_history(s: Session, task_id: int, from_status: str, to_status: str,
                            *, changed_by: int | None = None, reason: str = "") -> None:
    """写 TaskStatusHistory 一条(供 claim_development_task 等不走 SM 的路径用)。"""
    s.add(TaskStatusHistory(
        task_id=task_id, from_status=from_status, to_status=to_status,
        changed_by=changed_by, reason=reason or "",
    ))


# ---- Task CRUD -----------------------------------------------------------

def create_task(
    s: Session, *, project_id: int, story_id: int | None, title: str,
    type: str = ItemType.DEV, description: str = "", spec: str = "",
    priority: str = Priority.MEDIUM, sprint_id: int | None = None,
    assignee_id: int | None = None, due_date=None, labels: str = "[]",
    estimate: float | None = None,
    needed_capabilities="[]", complexity: int | None = None,
    domain_tags="[]", assignment_mode: str = "claim",
) -> Task:
    project = s.get(models.Project, project_id)
    if not project:
        raise NotFound(f"project {project_id} not found")
    if story_id is not None:
        story = s.get(models.Story, story_id)
        if not story:
            raise NotFound(f"story {story_id} not found")
        epic = s.get(models.Epic, story.epic_id)
        if epic is None or epic.project_id != project_id:
            raise InvalidValue(f"story {story_id} does not belong to project {project_id}")
    _check_type(type)
    _check_priority(priority)
    if sprint_id is not None:
        sp = s.get(models.Sprint, sprint_id)
        if not sp or sp.project_id != project_id:
            raise InvalidValue(f"sprint {sprint_id} does not belong to project {project_id}")
        if sp.status == SprintStatus.COMPLETED:
            raise InvalidValue("cannot assign task to a completed sprint")
    if assignee_id is not None:
        user = s.get(models.User, assignee_id)
        if not user:
            raise InvalidValue(f"assignee {assignee_id} not found")
    if labels:
        try:
            json.loads(labels)
        except json.JSONDecodeError:
            raise InvalidValue("labels must be a valid JSON array")
    t = Task(
        project_id=project_id, story_id=story_id, sprint_id=sprint_id,
        title=_required(title, "title", 300),
        type=type, description=description or "", spec=spec or "", priority=priority,
        assignee_id=assignee_id, due_date=_parse_due_date(due_date),
        labels=labels or "[]", estimate=estimate,
        needed_capabilities=json.dumps(
            normalize_required_capabilities(needed_capabilities), ensure_ascii=False
        ),
        complexity=normalize_complexity(complexity),
        domain_tags=json.dumps(normalize_domain_tags(domain_tags), ensure_ascii=False),
        assignment_mode=normalize_assignment_mode(assignment_mode),
    )
    s.add(t)
    _commit(s)
    s.refresh(t)
    _invalidate_project_stats_cache(project_id)
    return t


def get_task(s: Session, id: int) -> Task | None:
    return s.get(Task, id)


def list_tasks(
    s: Session, story_id: int | None = None, sprint_id: int | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[Task]:
    q = s.query(Task)
    if story_id is not None:
        q = q.filter(Task.story_id == story_id)
    if sprint_id is not None:
        q = q.filter(Task.sprint_id == sprint_id)
    q = q.order_by(Task.id.desc())
    return _paginate(q, limit, offset).all()


def query_task_count(
    s: Session, story_id: int | None = None, sprint_id: int | None = None,
) -> int:
    q = s.query(func.count(Task.id))
    if story_id is not None:
        q = q.filter(Task.story_id == story_id)
    if sprint_id is not None:
        q = q.filter(Task.sprint_id == sprint_id)
    return q.scalar() or 0


def list_task_status_history(s: Session, task_id: int, limit: int = 100) -> list[TaskStatusHistory]:
    return (
        s.query(TaskStatusHistory)
        .filter(TaskStatusHistory.task_id == task_id)
        .order_by(TaskStatusHistory.id.desc())
        .limit(limit)
        .all()
    )


# ---- Task 状态机驱动 ----------------------------------------------------

def set_status(
    s: Session, id: int, new_status: str, *,
    changed_by: int | None = None, reason: str = "",
    status_reason: str | None = None,
) -> Task | None:
    """任务状态变更(Story 265 收敛后,委托给 TaskStateMachine)。

    校验/副作用/历史/缓存失效全部由 SM 统一管理。
    """
    t = s.get(Task, id)
    if not t:
        raise NotFound(f"task {id} not found")
    _check_status(new_status)
    # 调用方传入的 status_reason 优先(覆盖 entity 上现有的)
    if status_reason is not None:
        t.status_reason = status_reason
    execute_transition(s, t, new_status, changed_by=changed_by, reason=reason)
    _commit(s)
    s.refresh(t)
    # Epic 140 切片 1：终态（done/blocked）自动沉淀能力评分 outcome（幂等）
    # 8/17 review P1/P2 修复：返回 outcome 用于上游判「是否值得 judge」。
    # 非终态调用 outcome 为 None → 不会触发 schedule_judge，省掉
    # 「spawn thread + new session + load task + judge_task() → return None」
    # 链路的纯开销。
    outcome = _record_learning_outcome(s, t)
    # Epic 140 切片 2：仅当 outcome 落库成功（即任务到了终态）才异步触发 L3
    # LLM judge。daemon 线程，失败吞异常；可用 AGENTBOARD_JUDGE_AUTO=0 关闭。
    if (
        outcome is not None
        and _os.environ.get("AGENTBOARD_JUDGE_AUTO", "1") == "1"
    ):
        try:
            from ..learning.judge import schedule_judge
            schedule_judge(t.id)
        except Exception:
            pass  # judge 属增强数据，调度失败不影响状态流转
    return t


def _record_learning_outcome(s: Session, t: Task):
    """终态任务落 task_outcome（延迟 import 避开 features 间循环依赖；失败不阻断主流程）。

    返回 ``TaskOutcome | None``：
    - 非终态 / 落库失败 → 返回 None（调用方据此跳过 judge 调度）
    - 终态落库成功 → 返回 outcome（调用方据此触发 judge 调度）

    Epic 140 切片 3 扩展：同步落 episode（向量化快照）+ 追加 project playbook pattern。
    两者均由 learning 模块内部静默降级，绝不因记忆写入失败回滚状态流转。
    幂等由 ProjectPlaybookEpisode 复合主键保证（详见 migration e5f6a7b8c9d0）。
    """
    try:
        from ..learning.service import record_outcome
        outcome = record_outcome(s, t)
        # Epic 140 切片 3：终态任务沉淀 episode + playbook（DB 级幂等，失败静默）
        if outcome is not None:
            try:
                from ..learning import memory as learning_memory
                ep_outcome = "success" if t.status == Status.DONE else "fail"
                learning_memory.store_episode(
                    s, t, score=outcome.score, outcome=ep_outcome,
                )
                # 传 episode_id=t.id 让 update_playbook 走 ProjectPlaybookEpisode
                # 复合主键（DB 唯一约束）跳过重复追加，跨并发也安全。
                learning_memory.update_playbook(
                    s,
                    project_id=t.project_id,
                    task_type=t.type or "dev",
                    summary=f"task#{t.id}: {t.title or ''}（{t.status}）",
                    outcome=ep_outcome,
                    episode_id=t.id,
                )
            except Exception:
                pass  # 记忆是增强数据，失败不影响 outcome/状态
        _commit(s)
        return outcome
    except Exception:
        # outcome 属增强数据，落库失败不应影响任务状态流转本身
        s.rollback()
        return None


# ---- 认领 / 提交评审 ---------------------------------------------------

def _legacy_claim_development_task(s: Session, task_id: int, *, user_id: int) -> Task:
    """开发任务竞争认领(Epic 122 切片 2 M1,CAS 并发安全;Story 265 后仅 todo 可认领)。

    条件 UPDATE ``status=todo`` → in_progress + assignee,rowcount=1 才成功;
    绕开状态机(系统操作),写一条 TaskStatusHistory。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status != Status.TODO:
        raise InvalidValue(
            f"task {task_id} already claimed or not claimable (status={t.status})"
        )
    old_status = t.status
    r = s.execute(
        update(Task).where(
            Task.id == task_id, Task.status == Status.TODO,
        ).values(status=Status.IN_PROGRESS, assignee_id=user_id)
    )
    if r.rowcount != 1:
        s.rollback()
        cur = s.get(Task, task_id)
        raise InvalidValue(
            f"task {task_id} claim conflict: already claimed "
            f"(status={cur.status if cur else 'deleted'})"
        )
    _record_status_history(
        s, task_id, str(old_status), str(Status.IN_PROGRESS),
        changed_by=user_id, reason="claim",
    )
    _commit(s)
    s.refresh(t)
    _invalidate_project_stats_cache(t.project_id)
    return t


def try_assign_task(
    s: Session,
    task_id: int,
    *,
    user_id: int | None,
    agent_registry_id: int | None = None,
    source: str,
    match_score: float | None = None,
    match_reason: dict | str | None = None,
    commit: bool = True,
) -> tuple[Task, TaskAssignment]:
    """Atomically reserve a todo task and persist its exact execution owner."""
    task = s.get(Task, task_id)
    if not task:
        raise NotFound(f"task {task_id} not found")
    if task.status != Status.TODO or task.current_assignment_id is not None:
        raise InvalidValue(
            f"task {task_id} already claimed or not claimable (status={task.status})"
        )
    agent = s.get(Agent, agent_registry_id) if agent_registry_id is not None else None
    if agent_registry_id is not None and agent is None:
        raise InvalidValue(f"agent registry id {agent_registry_id} not found")
    if agent is not None and user_id is not None and agent.user_id != user_id:
        raise InvalidValue(f"agent '{agent.agent_id}' belongs to another user")
    if (
        source == "claim"
        and agent_registry_id is not None
        and task.assignment_mode == "arbitrated"
    ):
        raise InvalidValue(
            f"task {task_id} uses arbitrated assignment; apply before claiming"
        )

    reason_json = (
        match_reason
        if isinstance(match_reason, str)
        else json.dumps(match_reason or {}, ensure_ascii=False, sort_keys=True)
    )
    assignment = TaskAssignment(
        task_id=task_id,
        agent_registry_id=agent_registry_id,
        user_id=user_id,
        source=source,
        status="active",
        active_slot="active",
        match_score=match_score,
        match_reason=reason_json,
    )
    try:
        s.add(assignment)
        s.flush()
        result = s.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.status == Status.TODO,
                Task.current_assignment_id.is_(None),
            )
            .values(
                status=Status.IN_PROGRESS,
                assignee_id=user_id,
                current_assignment_id=assignment.id,
            )
        )
        if result.rowcount != 1:
            s.rollback()
            current = s.get(Task, task_id)
            raise InvalidValue(
                f"task {task_id} claim conflict: already claimed "
                f"(status={current.status if current else 'deleted'})"
            )
        _record_status_history(
            s,
            task_id,
            str(Status.TODO),
            str(Status.IN_PROGRESS),
            changed_by=user_id,
            reason=source,
        )
        if commit:
            _commit(s)
        else:
            s.flush()
    except IntegrityError as exc:
        s.rollback()
        raise InvalidValue(
            f"task {task_id} claim conflict: already claimed"
        ) from exc

    s.refresh(task)
    s.refresh(assignment)
    _invalidate_project_stats_cache(task.project_id)
    return task, assignment


def claim_development_task(
    s: Session,
    task_id: int,
    *,
    user_id: int,
    agent_registry_id: int | None = None,
    source: str = "claim",
) -> Task:
    """Backward-compatible claim wrapper around the unified assignment CAS."""
    task, _assignment = try_assign_task(
        s,
        task_id,
        user_id=user_id,
        agent_registry_id=agent_registry_id,
        source=source,
    )
    return task


def finalize_task_assignment(
    s: Session, task: Task, *, commit: bool = True,
) -> TaskAssignment | None:
    """Close the active slot while retaining the assignment audit pointer."""
    if task.current_assignment_id is None:
        return None
    assignment = s.get(TaskAssignment, task.current_assignment_id)
    if assignment is None or assignment.status != "active":
        return assignment
    assignment.status = "completed"
    assignment.active_slot = None
    assignment.completed_at = utc_now()
    if commit:
        _commit(s)
    else:
        s.flush()
    return assignment


def apply_for_task(
    s: Session,
    task_id: int,
    *,
    user_id: int,
    agent_registry_id: int,
) -> TaskApplication:
    """Create or refresh one Agent's application for an arbitrated task."""
    task = s.get(Task, task_id)
    if not task:
        raise NotFound(f"task {task_id} not found")
    if task.assignment_mode != "arbitrated":
        raise InvalidValue(f"task {task_id} does not accept applications")
    if task.status != Status.TODO or task.current_assignment_id is not None:
        raise InvalidValue(f"task {task_id} is no longer open for applications")
    agent = s.get(Agent, agent_registry_id)
    if not agent:
        raise InvalidValue(f"agent registry id {agent_registry_id} not found")
    if agent.user_id != user_id:
        raise InvalidValue(f"agent '{agent.agent_id}' belongs to another user")

    result = score_agent_for_task(s, agent, task, role="developer")
    if not result.eligible:
        raise InvalidValue(f"agent '{agent.agent_id}' is not eligible: {result.reason}")
    application = (
        s.query(TaskApplication)
        .filter(
            TaskApplication.task_id == task_id,
            TaskApplication.agent_registry_id == agent_registry_id,
        )
        .first()
    )
    if application is None:
        application = TaskApplication(
            task_id=task_id,
            agent_registry_id=agent_registry_id,
            user_id=user_id,
        )
        s.add(application)
    application.user_id = user_id
    application.score = result.score
    application.reason = result.reason
    application.status = "pending"
    application.resolved_at = None
    try:
        _commit(s)
    except IntegrityError:
        s.rollback()
        application = (
            s.query(TaskApplication)
            .filter(
                TaskApplication.task_id == task_id,
                TaskApplication.agent_registry_id == agent_registry_id,
            )
            .one()
        )
        application.user_id = user_id
        application.score = result.score
        application.reason = result.reason
        application.status = "pending"
        application.resolved_at = None
        _commit(s)
    s.refresh(application)
    return application


def arbitrate_task(
    s: Session, task_id: int,
) -> tuple[Task, TaskAssignment, TaskApplication]:
    """Select the best pending application and assign the task in one transaction."""
    task = s.get(Task, task_id)
    if not task:
        raise NotFound(f"task {task_id} not found")
    if task.assignment_mode != "arbitrated":
        raise InvalidValue(f"task {task_id} does not use arbitrated assignment")
    if task.status != Status.TODO or task.current_assignment_id is not None:
        raise InvalidValue(f"task {task_id} is no longer open for arbitration")

    pending = (
        s.query(TaskApplication)
        .filter(
            TaskApplication.task_id == task_id,
            TaskApplication.status == "pending",
        )
        .all()
    )
    candidates: list[tuple[TaskApplication, Agent]] = []
    now = utc_now()
    for application in pending:
        agent = s.get(Agent, application.agent_registry_id)
        if agent is None:
            application.status = "rejected"
            application.resolved_at = now
            continue
        result = score_agent_for_task(s, agent, task, role="developer")
        application.score = result.score
        application.reason = result.reason
        if result.eligible:
            candidates.append((application, agent))
        else:
            application.status = "rejected"
            application.resolved_at = now
    if not candidates:
        _commit(s)
        raise InvalidValue(f"task {task_id} has no eligible pending applications")

    candidates.sort(key=lambda item: (-item[0].score, item[1].id))
    winner, agent = candidates[0]
    assigned_task, assignment = try_assign_task(
        s,
        task_id,
        user_id=winner.user_id,
        agent_registry_id=agent.id,
        source="arbitration",
        match_score=winner.score,
        match_reason=winner.reason,
        commit=False,
    )
    for application in pending:
        application.status = "accepted" if application.id == winner.id else "rejected"
        application.resolved_at = now
    _commit(s)
    s.refresh(assigned_task)
    s.refresh(assignment)
    s.refresh(winner)
    return assigned_task, assignment, winner


def submit_task_for_review(
    s: Session, task_id: int, *, user_id: int, is_admin: bool = False,
) -> Task:
    """开发完成提交评审(Epic 122 切片 2 M1)。

    - 校验 status == in_progress
    - assignee 匹配(admin 豁免)
    - 走 set_status 触发状态机迁移
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status != Status.IN_PROGRESS:
        raise InvalidValue(
            f"task {task_id} is not in_progress (current status: {t.status})"
        )
    if not is_admin and t.assignee_id != user_id:
        raise InvalidValue(
            f"task {task_id} is assigned to user#{t.assignee_id}, "
            "only the assignee (or admin) can submit for review"
        )
    return set_status(s, task_id, Status.IN_REVIEW)


# ---- 同步自 service.py ----
def _now():
    from datetime import datetime, UTC
    return datetime.now(UTC).replace(tzinfo=None)

# ---- 同步自 service.py ----
def _attachment_dir() -> str:
    _os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    return ATTACHMENT_DIR

# ---- 同步自 service.py ----
def batch_update_task_status(s: Session, task_ids: list[int], new_status: str,
                             *, changed_by: int | None = None,
                             status_reason: str | None = None) -> dict:
    """批量更新任务状态，返回成功和失败的任务ID列表（Story 265 后校验 status_reason）。"""
    _check_status(new_status)
    new = Status(new_status)
    new_reason = _validate_status_reason(new, status_reason)
    updated = []
    errors = []
    # transitions_for 定义于顶层 service.py（Phase 9 未迁移），函数内延迟导入避免循环
    from ...service import transitions_for
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
def remove_task_dependency(s: Session, dependency_id: int) -> None:
    """移除任务依赖关系。"""
    dep = s.get(TaskDependency, dependency_id)
    if not dep:
        raise NotFound(f"dependency {dependency_id} not found")
    s.delete(dep)
    _commit(s)

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
def import_tasks_from_json(s: Session, project_id: int, data: dict) -> dict:
    """从 JSON 数据导入任务。

    8/17 review 修复：
    - P1 #1（首次）：默认值与 model CheckConstraint 对齐。
    - P1 #2（本轮）：每条 item 用 ``s.begin_nested()`` 包成 SAVEPOINT——单条
      失败只回滚自身，**不影响**同批其它已 flush 成功但未 commit 的合法条目。

    注意：此函数当前未在 service.py 末尾 re-bind 列表里，live 调用走
    service.py:2212。本副本需与 live 保持一致，避免未来 rebind 后回退
    到旧默认值。
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
                # 8/17 review：priority 也用枚举常量默认，保持三处一致。
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
            # SAVEPOINT 已自动回滚，session 状态干净。
            errors.append({"title": item.get("title", "?"), "error": str(e)})
    # 外层一次性 commit。
    _commit(s)
    return {"imported": imported, "errors": errors}

# ---- 同步自 service.py ----
def _comment_target(
    s: Session, *, task_id: int | None, story_id: int | None, epic_id: int | None
) -> dict:
    """校验评论挂载目标：task/story/epic 三者恰好其一非空，且实体存在。"""
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

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
def delete_comment(s: Session, id: int) -> bool:
    comment = s.get(Comment, id)
    if not comment:
        return False
    s.delete(comment); _commit(s); return True

# ---- 同步自 service.py ----
def _task_needs_design(s: Session, t: Task) -> bool:
    """Task 所属 Story 是否需要设计评审段（Epic 123）；无 Story 视为快速流（false）。"""
    if t.story_id is None:
        return False
    story = s.get(Story, t.story_id)
    return bool(story and story.needs_design)

# ---- 同步自 service.py ----
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

# ---- 同步自 service.py ----
def generate_tasks_from_spec(s: Session, task_id: int) -> list:
    """解析任务 spec 中的清单项（- [ ] 标题），生成同级子任务。

    生成的子任务：同 project / story，type=dev（ItemType.DEV），
    status=todo（Status.TODO，Task model 默认值），priority=medium
    （Priority.MEDIUM），并通过 source_spec_id 反向关联到源任务；
    同时在源 spec 末尾回写链接。
    8/17 review P1：注释里的旧 "type=task / status=backlog" 表述已下线
    （Story 265 收敛），以实际 model 默认值/代码为准。
    """
    src = s.get(Task, task_id)
    if not src:
        raise NotFound(f"task {task_id} not found")
    existing_titles = {
        title for (title,) in s.query(Task.title).filter(Task.source_spec_id == task_id).all()
    }
    created = []
    for line in (src.spec or "").splitlines():
        m = re.match(r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)", line)
        if not m:
            continue
        title = m.group(1).strip()
        if not title:
            continue
        title = title[:300]
        if title in existing_titles:
            continue
        t = Task(project_id=src.project_id, story_id=src.story_id,
                 type=ItemType.DEV, title=title[:300], description=title,
                 source_spec_id=task_id)
        s.add(t)
        created.append(t)
        existing_titles.add(title)
    if created:
        s.flush()
        links = "\n".join(f"- 子任务 #{t.id}: {t.title}" for t in created)
        src.spec = (src.spec or "") + f"\n\n## 生成的子任务\n{links}\n"
    _commit(s)
    for t in created:
        s.refresh(t)
    if created:
        s.refresh(src)
    return created
