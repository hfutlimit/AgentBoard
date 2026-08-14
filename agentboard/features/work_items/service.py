"""WorkItems service:Task / Comment / Attachment / Dependency。

Phase 4 第二段:从 service.py 拆出。set_status 用 Phase 3 的 TaskStateMachine
统一驱动,校验/副作用/历史/缓存失效自动跑。

老 import 路径兼容:service.py 末尾重绑 set_status / create_task / get_task /
list_tasks / claim_development_task / submit_task_for_review 等到本模块。
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容
from ...core.common.enums import (
    ItemType, Priority, SprintStatus, Status, StatusReason,
)
from ...core.exceptions import Conflict, InvalidValue, NotFound
from ...core.service_helpers import (
    _check_priority, _check_status, _check_type, _commit,
    _invalidate_project_stats_cache, _paginate, _parse_due_date, _required,
)
from .models import Comment, Task, TaskStatusHistory
from .state_machine import execute_transition

log = logging.getLogger("agentboard.features.work_items.service")


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
    return t


# ---- 认领 / 提交评审 ---------------------------------------------------

def claim_development_task(s: Session, task_id: int, *, user_id: int) -> Task:
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
