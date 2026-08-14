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
from ...core.exceptions import Conflict, InvalidValue, NotFound
from .models import Comment, Task, TaskStatusHistory
from .state_machine import execute_transition

log = logging.getLogger("agentboard.features.work_items.service")

from ...core.common.enums import (
    ItemType, Priority, SprintStatus, Status, StatusReason,
    ItemType,
    Priority,
    SprintStatus,
    Status,
)
from ...core.common.models import utc_now  # noqa: F401
from ..scheduling.models import (  # noqa: F401  (跨域常量)
    DEFAULT_REVIEW_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_SCAN_BATCH,
)

from ...core.service_helpers import (
    _check_priority, _check_status, _check_type, _commit,
    _invalidate_project_stats_cache, _paginate, _parse_due_date, _required,
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


# ---- 同步自 service.py ----
def _now():
    from datetime import datetime, UTC
    return datetime.now(UTC).replace(tzinfo=None)

# ---- 同步自 service.py ----
def _attachment_dir() -> str:
    _os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    return ATTACHMENT_DIR

# ---- 同步自 service.py ----
def scan_review_timeouts(s: Session, *, project_id: int | None = None,
                         timeout_minutes: int = DEFAULT_REVIEW_TIMEOUT_MINUTES,
                         max_per_run: int = DEFAULT_TIMEOUT_SCAN_BATCH,
                         now: datetime | None = None) -> dict:
    """评审超时自愈扫描（S3 M2 护栏）。

    超时定义：pending_review Story / in_review Task 且 reviewer 已指派且「最后活动」
    超时 —— Story 最后活动 = max(created_at, 最新评论时间)；Task 用 updated_at。
    处理：轮次达 MAX_REVIEW_ROUNDS → blocked（护栏终态）；否则 CAS 解绑旧 reviewer →
    重新随机指派（排除旧 reviewer，Task 版额外排除 assignee）；无候选 → 保持解绑
    由下轮轮询补派。解绑 CAS 带旧 reviewer_id 仲裁，多 worker 并发恰一赢家。
    """
    now = now or utc_now()
    timeout = timedelta(minutes=max(1, timeout_minutes))
    result = {"stories_reassigned": 0, "tasks_reassigned": 0,
              "blocked": 0, "no_candidate": 0,
              # S3 M3：majority 模式超时按现有票兜底结算计数（防死锁）
              "stories_settled": 0, "tasks_settled": 0,
              # 内部重派详情（(entity_id, new_reviewer_id)），供 API 层发布事件，响应中剔除
              "_stories_reassigned": [], "_tasks_reassigned": []}

    st_q = s.query(Story).filter(
        Story.status == "pending_review",
        Story.reviewer_id.isnot(None),
    )
    if project_id is not None:
        st_q = st_q.join(Epic, Story.epic_id == Epic.id).filter(
            Epic.project_id == project_id)
    overdue_stories = [
        st for st in st_q.order_by(Story.id.asc()).limit(max_per_run).all()
        if now - _story_last_activity(s, st) > timeout
    ]
    for st in overdue_stories:
        # S3 M3：majority 模式超时按现有票兜底结算（approve>reject → 通过；
        # reject>=approve → 驳回；平局保守驳回防死锁）。零票走既有重派逻辑。
        if get_review_mode() == REVIEW_MODE_MAJORITY:
            approve_n, reject_n = _review_vote_counts(s, "story", st.id)
            if approve_n + reject_n > 0:
                if approve_n > reject_n:
                    _settle_majority_approved(s, st, "story")
                    result["stories_settled"] += 1
                else:
                    settled = _settle_majority_rejected(s, st, "story")
                    result["stories_settled"] += 1
                    if settled.status == "blocked":
                        result["blocked"] += 1
                continue
        if (st.review_round or 0) >= MAX_REVIEW_ROUNDS:
            r = s.execute(update(Story).where(
                Story.id == st.id,
                Story.status == "pending_review",
            ).values(status="blocked"))
            if r.rowcount == 1:
                _commit(s)
                result["blocked"] += 1
            else:
                s.rollback()
            continue
        old = st.reviewer_id
        r = s.execute(update(Story).where(
            Story.id == st.id,
            Story.reviewer_id == old,
        ).values(reviewer_id=None))
        if r.rowcount != 1:
            s.rollback()
            continue  # 并发写者已抢先处理
        _commit(s)
        fresh = s.get(Story, st.id)
        if fresh is None:
            continue
        new_rev = _reassign_story_reviewer(s, fresh, exclude_user_id=old)
        if new_rev is not None:
            result["stories_reassigned"] += 1
            result["_stories_reassigned"].append((fresh.id, new_rev))
        else:
            result["no_candidate"] += 1

    t_q = s.query(Task).filter(
        Task.status == Status.IN_REVIEW,
        Task.reviewer_id.isnot(None),
    )
    if project_id is not None:
        t_q = t_q.filter(Task.project_id == project_id)
    overdue_tasks = [
        t for t in t_q.order_by(Task.id.asc()).limit(max_per_run).all()
        if now - t.updated_at > timeout
    ]
    for t in overdue_tasks:
        # S3 M3：majority 模式超时按现有票兜底结算（语义同 Story 分支）
        if get_review_mode() == REVIEW_MODE_MAJORITY:
            approve_n, reject_n = _review_vote_counts(s, "task", t.id)
            if approve_n + reject_n > 0:
                if approve_n > reject_n:
                    _settle_majority_approved(s, t, "task")
                    result["tasks_settled"] += 1
                else:
                    settled = _settle_majority_rejected(s, t, "task")
                    result["tasks_settled"] += 1
                    if settled.status == Status.BLOCKED:
                        result["blocked"] += 1
                continue
        if (t.review_round or 0) >= MAX_REVIEW_ROUNDS:
            r = s.execute(update(Task).where(
                Task.id == t.id,
                Task.status == Status.IN_REVIEW,
            ).values(status=Status.BLOCKED))
            if r.rowcount == 1:
                _record_status_history(s, t.id, str(Status.IN_REVIEW), str(Status.BLOCKED),
                                       reason="timeout max review rounds")
                _commit(s)
                result["blocked"] += 1
            else:
                s.rollback()
            continue
        old = t.reviewer_id
        r = s.execute(update(Task).where(
            Task.id == t.id,
            Task.reviewer_id == old,
        ).values(reviewer_id=None))
        if r.rowcount != 1:
            s.rollback()
            continue
        _commit(s)
        fresh = s.get(Task, t.id)
        if fresh is None:
            continue
        new_rev = _reassign_task_reviewer(s, fresh, exclude_user_id=old)
        if new_rev is not None:
            result["tasks_reassigned"] += 1
            result["_tasks_reassigned"].append((fresh.id, new_rev))
        else:
            result["no_candidate"] += 1
    return result

# ---- 同步自 service.py ----
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
    """从 JSON 数据导入任务。"""
    import json
    imported = []
    errors = []
    tasks_data = data.get("tasks", [])
    for item in tasks_data:
        try:
            title = _required(item.get("title", "").strip(), "title", 300)
            task = Task(
                project_id=project_id,
                title=title,
                type=item.get("type", "task"),
                description=item.get("description", ""),
                priority=item.get("priority", "medium"),
                status=item.get("status", "backlog"),
            )
            s.add(task)
            s.flush()
            imported.append({"id": task.id, "title": task.title})
        except Exception as e:
            errors.append({"title": item.get("title", "?"), "error": str(e)})
    _commit(s)
    return {"imported": imported, "errors": errors}

# ---- 同步自 service.py ----
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
def assign_task_reviewer(s: Session, task_id: int, *, user_id: int | None = None,
                         is_admin: bool = False) -> Task:
    """随机指派 Task 评审人（幂等；CAS 并发安全）。

    与 Story 版 assign_reviewer 同构：
    - 候选 = 在线 ∩ 角色含 reviewer ∩ 绑定用户属项目成员，且 **≠ assignee**
      （评审人与作者隔离，文档 #51 要求）；
    - CAS 条件 UPDATE ``status=in_review AND reviewer_id IS NULL`` →
      ``reviewer_id=候选``，rowcount=1 才成功；并发下另一个写者获胜时回查返回其指派结果；
    - 幂等：已指派（reviewer_id 非空）直接返回现态，不换人。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.reviewer_id is not None:
        return t  # 幂等：已指派（含 reject 退回后复用同一 reviewer）
    if t.status != Status.IN_REVIEW:
        raise InvalidValue(
            f"task {task_id} is not in_review (current status: {t.status})")
    candidates = _online_reviewer_candidates(s, t.project_id)
    candidates = [a for a in candidates if a.user_id != t.assignee_id]
    if not candidates:
        raise InvalidValue(
            "no online reviewer available (register an online reviewer agent first)")
    reviewer = random.choice(candidates)
    r = s.execute(
        update(Task).where(
            Task.id == task_id,
            Task.reviewer_id.is_(None),
            Task.status == Status.IN_REVIEW,
        ).values(reviewer_id=reviewer.user_id)
    )
    if r.rowcount != 1:
        # 并发写者已抢先指派：回查返回现态
        s.rollback()
        return s.get(Task, task_id)
    _commit(s)
    s.refresh(t)
    return t

# ---- 同步自 service.py ----
def review_task(s: Session, *, task_id: int, reviewer_user_id: int,
                verdict: str, comment: str) -> Task:
    """Task 评审投票（CAS）：仅被指派 reviewer 可操作 in_review 任务。

    - approve：in_review → done（评审通过，任务完成）；
    - reject ：review_round + 1，任务退回 in_progress（开发者修复后重新
      submit-review，reviewer_id 保留 → 同一 reviewer 继续评审）；评论记录意见；
    - 护栏：review_round 达 MAX_REVIEW_ROUNDS → blocked（待人工仲裁）。
    - S3 M3：review_mode=majority 时改为多数决投票（_vote_majority），
      投票人资格放宽为项目在线 reviewer 候选（≠assignee），达法定票数按多数结算。

    评论是评审意见唯一载体（approve/reject 必须伴随 comment），形成审计轨迹。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if verdict not in ("approve", "reject"):
        raise InvalidValue(f"invalid verdict '{verdict}' (expected approve|reject)")
    comment = (comment or "").strip()
    if not comment:
        raise InvalidValue("review comment is required (approve/reject must carry a comment)")
    # S3 M3：多数决模式走投票分支（未达法定票数不结算，状态保持）
    if get_review_mode() == REVIEW_MODE_MAJORITY:
        t, _settled = _vote_majority(
            s, t, entity_type="task", reviewer_user_id=reviewer_user_id,
            verdict=verdict, comment=comment)
        return t
    if t.reviewer_id != reviewer_user_id:
        raise InvalidValue("only the assigned reviewer can review this task")
    if t.status != Status.IN_REVIEW:
        raise InvalidValue(f"task is not in_review (current status: {t.status})")
    reviewer = s.get(User, reviewer_user_id)
    reviewer_name = reviewer.display_name or reviewer.username if reviewer else f"user#{reviewer_user_id}"

    if verdict == "approve":
        r = s.execute(
            update(Task).where(
                Task.id == task_id,
                Task.reviewer_id == reviewer_user_id,
                Task.status == Status.IN_REVIEW,
            ).values(status=Status.DONE)
        )
        if r.rowcount != 1:
            s.rollback()
            raise InvalidValue("review conflict: task state changed concurrently")
        _record_status_history(s, task_id, str(Status.IN_REVIEW), str(Status.DONE),
                               changed_by=reviewer_user_id, reason="review approve")
        _commit(s)
    else:  # reject
        new_round = (t.review_round or 0) + 1
        target = Status.BLOCKED if new_round >= MAX_REVIEW_ROUNDS else Status.IN_PROGRESS
        r = s.execute(
            update(Task).where(
                Task.id == task_id,
                Task.reviewer_id == reviewer_user_id,
                Task.status == Status.IN_REVIEW,
            ).values(review_round=new_round, status=target)
        )
        if r.rowcount != 1:
            s.rollback()
            raise InvalidValue("review conflict: task state changed concurrently")
        _record_status_history(s, task_id, str(Status.IN_REVIEW), str(target),
                               changed_by=reviewer_user_id,
                               reason=f"review reject round={new_round}")
        _commit(s)
    # 评审意见落评论（唯一载体）
    create_comment(s, author=reviewer_name, content=comment, task_id=task_id)
    _invalidate_project_stats_cache(t.project_id)
    s.refresh(t)
    return t

# ---- 同步自 service.py ----
def generate_tasks_from_spec(s: Session, task_id: int) -> list:
    """解析任务 spec 中的清单项（- [ ] 标题），生成同级子任务。

    生成的子任务：同 project / story，type=task，status=backlog，
    并通过 source_spec_id 反向关联到源任务；同时在源 spec 末尾回写链接。
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