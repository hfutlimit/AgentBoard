"""Work_Items feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ...core.application import service
from ...core.api.schemas import CommentIn, LeaseReclaimIn, StatusIn
from .schemas import (
	AgentReviewIn,
	BulkTaskDelete,
	BulkTaskUpdate,
	ReassignTimeoutIn,
	SpecAppendIn,
	TaskClaimIn,
	TaskPatch,
)
import os
from ...models import Status
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.
from ...core.infrastructure import messaging as mq  # publish_workflow_event + EVENT_* constants
from ...mq import (
    EVENT_TASK_AVAILABLE, EVENT_TASK_ASSIGNED, EVENT_TASK_READY_FOR_REVIEW,
    EVENT_TASK_REVIEWED, EVENT_TASK_REJECTED, EVENT_TASK_REVIEW_REQUESTED,
    EVENT_TASK_REVIEW_VOTE_CAST, EVENT_STORY_REVIEW_REQUESTED,
    # PR-4：internal 编排事件（Python workflow_worker 专属，触发 reviewer 分配）
    EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED,
    publish_workflow_event,
)
from ..scheduling.models import TaskAssignment

log = logging.getLogger(__name__)

router = APIRouter(tags=["work_items"])


# 必须声明在 /api/tasks/{tid} 系列之前，避免 "reclaim-stale" 被当作 tid 捕获。

@router.post("/api/tasks/reclaim-stale")
def reclaim_stale_tasks(
    body: LeaseReclaimIn | None = None, s: Session = Depends(get_session),
):
    """回收租约过期的 in_progress Task（持有 Worker 已崩溃），批量回退 todo。

    只回收 claim_development_task 写入租约（claimed_by 非空）的行 —— 人工
    认领/直派不受影响；且要求 updated_at < cutoff，认领后有后续流转
    （如评审驳回回退）的行一律保护。返回被回收的 task id 列表。
    """
    lease = (body.lease_seconds if body and body.lease_seconds is not None
             else service.DEFAULT_TASK_CLAIM_LEASE_SECONDS)
    try:
        ids = service.reclaim_stale_tasks(s, lease_seconds=lease)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"reclaimed": ids, "count": len(ids), "lease_seconds": lease}


@router.get("/api/tasks/search")
def search_tasks_enhanced_api(
    project_id: int | None = None,
    epic_id: int | None = None,
    story_id: int | None = None,
    sprint_id: int | None = None,
    type: str | None = Query(None),
    status: list[str] | None = Query(None),
    priority: list[str] | None = Query(None),
    q: str | None = Query(None),
    sort_by: str = Query("id", description="Sort field: id, created_at, updated_at, priority, status, title"),
    sort_order: str = Query("desc", pattern=r"^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
):
    """增强搜索：支持多值过滤（status[]=xx&status[]=yy）和排序。"""
    try:
        rows = service.search_tasks_enhanced(
            s, project_id=project_id, epic_id=epic_id, story_id=story_id,
            sprint_id=sprint_id, type=type, status=status, priority=priority,
            q=q, sort_by=sort_by, sort_order=sort_order, limit=limit, offset=offset,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(t) for t in rows]



@router.get("/api/tasks/{tid}")
def get_task(tid: int, authorization: str | None = Header(None),
             s: Session = Depends(get_session)):
    # P0-1: Task read auth matches the new `_authorize_task_read` contract;
    # unauthenticated callers in dev (REQUIRE_AUTH=0) still pass.
    api_helpers._authorize_task_read(authorization, s, tid)
    return service._ser(api_helpers._need(service.get_task(s, tid), "task"))



@router.patch("/api/tasks/{tid}")
def update_task(
    tid: int, body: TaskPatch, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    old_assignee_id = task.assignee_id if task else None
    old_status = task.status if task else None
    try:
        r = service.update_task(s, tid, **body.model_dump(exclude_unset=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.IllegalTransition as e:
        # Story 265：PATCH 改 status 也走状态机，非法迁移返回 400
        raise HTTPException(status_code=400, detail=str(e))
    if pid:
        api_helpers._invalidate_stats_cache(pid)
    updated = api_helpers._need(r, "task")
    if updated.assignee_id is not None and updated.assignee_id != old_assignee_id:
        service.create_notification(
            s, user_id=updated.assignee_id, notif_type="task_assigned",
            title=f"任务 #{updated.id} 已分配给你", content=updated.title,
            link=f"/task/{updated.id}",
        )
        # Agent MQ 定向投递（2026-08-09）：任务显式指派给某 agent 用户 →
        # 发到该 agent 的 direct queue（task.assigned），对应 worker 独享消费。
        _agent = s.query(service.Agent).filter(
            service.Agent.user_id == updated.assignee_id).first()
        if _agent is not None and _agent.agent_id:
            # PR-5：走 helper resolve worker_id from agent_id，routing 用
            # worker_id（物理身份，.NET worker 按 _identity.WorkerId 订阅）
            from ..scheduling.service import publish_workflow_event_for_agent
            publish_workflow_event_for_agent(
                s, EVENT_TASK_ASSIGNED, "task", updated.id,
                agent_id=_agent.agent_id,
                ref_id=updated.story_id,
                # P0-2：task type 进 body（.NET prompt 分语义）
                task_type=str(updated.type or "") or None,
            )
    if updated.assignee_id is not None and updated.status != old_status:
        service.create_notification(
            s, user_id=updated.assignee_id, notif_type="status_changed",
            title=f"任务 #{updated.id} 状态已变更", content=f"{updated.title}：{old_status} → {updated.status}",
            link=f"/task/{updated.id}",
        )
    return service._ser(updated)



@router.put("/api/tasks/{tid}/status")
def set_status(
    tid: int, body: StatusIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    old_status = task.status if task else None
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    try:
        result = service.set_status(s, tid, body.status, changed_by=uid,
                                    reason=body.reason, status_reason=body.status_reason)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    except service.InvalidValue as e:
        # Story 265：status_reason 校验失败 → 400
        raise HTTPException(status_code=400, detail=str(e))
    if pid:
        api_helpers._invalidate_stats_cache(pid)
    if result.assignee_id is not None and result.status != old_status:
        service.create_notification(
            s, user_id=result.assignee_id, notif_type="status_changed",
            title=f"任务 #{result.id} 状态已变更", content=f"{result.title}：{old_status} → {result.status}",
            link=f"/task/{result.id}",
        )
    return service._ser(result)


# ---------------------------------------------------------------------------
# Review 2026-08-26 P1 #3：admin 显式 force_complete endpoint
# ---------------------------------------------------------------------------

@router.post("/api/tasks/{tid}/admin/force-complete")
def admin_force_complete(
    tid: int,
    authorization: str | None = Header(None),
    reason: str = Query("manual_override", description="强制完成原因（写 history）"),
    s: Session = Depends(get_session),
):
    """Admin 强制完成 task（绕过 Review gate）。

    Review 2026-08-26 P1 #3：通用 set_status(DONE) 路径不再接受从 todo /
    in_progress 直接跳到 done；admin 显式 exceptional 场景（reviewer 全员失联、
    紧急 hotfix 等）必须走本 endpoint。status_reason 固定写入 "manual_override"，
    审计可见；其他角色（普通用户 / Agent）调用会被 403 拒绝。

    不接 async 回调、不发 workflow event —— admin 操作属人工仲裁，event 走
    普通 status_changed 通知即可。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="force-complete requires admin role",
        )
    try:
        t = service.force_complete_task(
            s, tid, admin_user_id=uid, reason=reason or "manual_override",
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        # 含 force_complete 状态不合法、admin 校验失败
        raise HTTPException(status_code=400, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
    return service._ser(t)


@router.get("/api/tasks/{tid}/status-history")
def get_task_status_history(tid: int, authorization: str | None = Header(None),
                            s: Session = Depends(get_session)):
    """任务状态变更历史（Epic 123）：from_status → to_status、操作人、原因、时间，倒序。"""
    api_helpers._authorize_task_read(authorization, s, tid)
    return [service._ser(h) for h in service.list_task_status_history(s, tid)]



@router.post("/api/tasks/{tid}/claim")
def claim_task_for_development(tid: int, authorization: str | None = Header(None),
                               body: TaskClaimIn | None = None,
                               s: Session = Depends(get_session)):
    """开发任务竞争认领（Epic 122 切片 2，CAS 并发安全）。

    条件 UPDATE ``status IN (todo,)``（Story 265 backlog 已下线）→ ``in_progress + assignee_id=当前用户``，
    rowcount=1 才成功；已认领/已结束返回 409 明确错误（复用 Epic 118 护栏语义）。
    项目写权限由 project_access_middleware 自动覆盖。
    body.agent（可省略，默认 "worker"）写入认领租约 claimed_by，
    供 /api/tasks/reclaim-stale 判定崩溃回收。
    """
    if not authorization and api_helpers._auth_is_required():
        raise HTTPException(status_code=401, detail="unauthorized")
    if not authorization:
        raise HTTPException(status_code=422, detail="claim requires login")
    actor = api_helpers.resolve_actor_context(
        authorization, s, required_permission="api:write",
    )
    try:
        t = service.claim_development_task(
            s,
            tid,
            user_id=actor.user_id,
            agent_registry_id=actor.agent_registry_id,
            claimed_by=(body.agent if body else "worker"),
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=409, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
    return service._ser(t)


@router.post("/api/tasks/{tid}/apply")
def apply_for_task(
    tid: int,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """Apply for an arbitrated task using the Agent bound to the credential."""
    actor = api_helpers.resolve_actor_context(
        authorization, s, required_permission="api:write",
    )
    if actor.agent_registry_id is None:
        raise HTTPException(
            status_code=422, detail="application requires an Agent-scoped API key",
        )
    try:
        application = service.apply_for_task(
            s,
            tid,
            user_id=actor.user_id,
            agent_registry_id=actor.agent_registry_id,
        )
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except service.InvalidValue as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return service._ser(application)


@router.post("/api/tasks/{tid}/arbitrate")
def arbitrate_task(
    tid: int,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """Assign an arbitrated task to its highest-ranked pending Agent."""
    task = service.get_task(s, tid)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    actor = api_helpers.resolve_actor_context(
        authorization, s, required_permission="api:write",
    )
    api_helpers._enforce_owner_or_admin(
        s, task.project_id, actor.user_id, actor.is_admin,
    )
    try:
        assigned_task, assignment, application = service.arbitrate_task(s, tid)
    except service.InvalidValue as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    agent = s.get(service.Agent, assignment.agent_registry_id)
    if agent is not None:
        # PR-5：走 helper resolve worker_id from agent_id，routing 用
        # worker_id（物理身份，.NET worker 按 _identity.WorkerId 订阅）
        from ..scheduling.service import publish_workflow_event_for_agent
        publish_workflow_event_for_agent(
            s,
            EVENT_TASK_ASSIGNED,
            "task",
            assigned_task.id,
            agent_id=agent.agent_id,
            ref_id=assigned_task.story_id,
            # P0-2：task type 进 body（.NET prompt 分语义）
            task_type=str(assigned_task.type or "") or None,
        )
    return {
        "task": service._ser(assigned_task),
        "assignment": service._ser(assignment),
        "application": service._ser(application),
    }



@router.post("/api/tasks/{tid}/submit-review")
def submit_task_review(tid: int, authorization: str | None = Header(None),
                       s: Session = Depends(get_session)):
    """开发完成提交评审（Epic 122 切片 2）：assignee 或 admin 操作。

    - 校验 status=in_progress + assignee 匹配（admin 豁免）→ in_review；
    - 成功 → 广播 ``task.ready_for_review``（分配器 worker 消费，切片 2 M2 指派 reviewer）。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="submit-review requires login")
    try:
        t = service.submit_task_for_review(s, tid, user_id=uid, is_admin=is_admin)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
    # Persist in_review before the internal coordinator can consume the event.
    s.commit()
    # 事件源：任务进入评审态 → 广播 task.ready_for_review（审计 / 通知用，
    # .NET 不再执行它 —— 它是 pre-assignment 事件没 reviewer）。
    publish_workflow_event(EVENT_TASK_READY_FOR_REVIEW, "task", t.id,
                           ref_id=t.assignee_id)
    # PR-6：design task（type='design' 且 needs_human_confirmation=True）
    # 跳过自动 reviewer 分配，state 保持 in_review 但等的是 user，不是
    # reviewer。user 走 POST /api/tasks/{tid}/user_confirm 确认进 done。
    # 其它 task 走原 PR-4 路径：internal 事件触发 Python workflow_worker
    # 选 reviewer，选完 assign-reviewer API publish task.review_requested
    # 到 agent 定向队列，.NET 拿那条去真正执行 review。
    if not t.needs_human_confirmation:
        publish_workflow_event(EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED, "task", t.id,
                               ref_id=t.assignee_id, route="internal")
    else:
        log.info("submit-review: task %s (type=%s) needs_human_confirmation=True，"
                 "跳过自动 reviewer 分配，等 user_confirm",
                 t.id, t.type)
    api_helpers._notify_webhooks(s, t.project_id, EVENT_TASK_READY_FOR_REVIEW,
                     {"id": t.id, "assignee_id": t.assignee_id, "status": t.status})
    return service._ser(t)


# ===================== PR-6: User confirmation gate for design tasks =====================
# design task 完成后等用户确认（POST /api/tasks/{tid}/user_confirm）才进 done，
# 这条路径下没有 reviewer agent 介入，dependency unlock 与 reviewer 路径走
# 完全相同的代码（service.get_unlocked_dependent_tasks → publish
# EVENT_TASK_AVAILABLE）。status_reason 仍按 5 值状态机要求填
# 'completed' / 'withdrawn' / 'manual_override'。

@router.post("/api/tasks/{tid}/user_confirm")
def user_confirm_task(
    tid: int,
    body: dict | None = None,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """PR-6：用户确认 design task（needs_human_confirmation=True）→ done。

    校验：
      - task 存在
      - status == in_review（必须先经过 submit-review）
      - needs_human_confirmation == True（不然这个端点对该 task 无意义）

    动作：
      - 状态 → done，status_reason=completed
      - 触发 dependency unlock（同 reviewer approve 路径）
      - 发布 EVENT_TASK_REVIEWED 让 .NET 知道
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="user_confirm requires login")
    t = s.get(service.Task, tid)
    if t is None:
        raise HTTPException(status_code=404, detail=f"task {tid} not found")
    if not t.needs_human_confirmation:
        raise HTTPException(
            status_code=409,
            detail=f"task {tid} 不需要 user 确认（needs_human_confirmation=False）",
        )
    if t.status != service.Status.IN_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"task {tid} status={t.status}，user_confirm 要求 in_review",
        )
    comment = (body or {}).get("comment", "")
    # set_status 走 TaskStateMachine：in_review → done，status_reason=completed
    t = service.set_status(
        s, tid, service.Status.DONE,
        changed_by=uid,
        status_reason=service.StatusReason.COMPLETED,
    )
    if t is None:
        raise HTTPException(status_code=409, detail=f"task {tid} state transition refused")
    # user 反馈写进 comment 留 trail
    if comment:
        try:
            service.create_comment(
                s, author=str(uid), content=comment, task_id=t.id,
            )
        except Exception as e:
            log.warning("user_confirm: task %s 写 comment 失败：%s", t.id, e)
    api_helpers._invalidate_stats_cache(t.project_id)
    # 触发 dependency unlock + PR-10 dispatch implementation task
    # 之前是 publish task.available 让 worker 抢；PR-10 改成 server 直接派：
    # 选 agent → 推 in_progress → publish task.assigned（4 字段齐全）
    try:
        from ..scheduling.service import dispatch_implementation_task
        for succ in service.get_unlocked_dependent_tasks(s, t.id):
            dispatch_implementation_task(s, succ.id)
    except Exception as e:
        log.warning("user_confirm: task %s dependency unlock dispatch 失败：%s",
                    t.id, e)
    publish_workflow_event(EVENT_TASK_REVIEWED, "task", t.id, ref_id=uid)
    api_helpers._notify_webhooks(s, t.project_id, EVENT_TASK_REVIEWED,
                     {"id": t.id, "by": uid, "decision": "confirmed"})
    return service._ser(t)


@router.post("/api/tasks/{tid}/user_reject")
def user_reject_task(
    tid: int,
    body: dict | None = None,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """PR-6：用户拒绝 design task → 回退 in_progress 让 assignee 改。

    校验：同 user_confirm（in_review + needs_human_confirmation=True）

    动作：
      - 状态 → in_progress（status_reason 不需要，in_progress 不强制填）
      - 写一条 comment 记录用户反馈（可选）
      - 不发 dependency unlock（task 还没 done）
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="user_reject requires login")
    t = s.get(service.Task, tid)
    if t is None:
        raise HTTPException(status_code=404, detail=f"task {tid} not found")
    if not t.needs_human_confirmation:
        raise HTTPException(
            status_code=409,
            detail=f"task {tid} 不需要 user 确认（needs_human_confirmation=False）",
        )
    if t.status != service.Status.IN_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"task {tid} status={t.status}，user_reject 要求 in_review",
        )
    feedback = (body or {}).get("comment", "")
    # in_review → in_progress 状态机允许（同一 assignee 改）
    t = service.set_status(
        s, tid, service.Status.IN_PROGRESS,
        changed_by=uid,
    )
    if t is None:
        raise HTTPException(status_code=409, detail=f"task {tid} state transition refused")
    # comment 留个 trail 让 assignee 看到反馈
    if feedback:
        try:
            service.create_comment(
                s, author=str(uid), content=feedback, task_id=t.id,
            )
        except Exception as e:
            log.warning("user_reject: task %s 写 comment 失败：%s", t.id, e)
    api_helpers._invalidate_stats_cache(t.project_id)
    return service._ser(t)



@router.post("/api/tasks/{tid}/assign-reviewer")
def assign_task_reviewer(tid: int, count: int = 1,
                         authorization: str | None = Header(None),
                         s: Session = Depends(get_session)):
    """随机指派 Task 评审人（幂等，CAS 并发安全，Epic 122 切片 2 M2）。

    - 候选 = 在线 reviewer ∩ 项目成员 ∩ ≠ assignee；无候选 → 422；
    - 成功 → 定向投递 review.requested（entity_type=task）给 reviewer 绑定的
      Agent 队列（无 Agent 绑定退化为广播，开发者轮询 list_review_tasks 兜底）。
    - **Sprint 12 多数决 fan-out**（``?count=N``）：一次指 N 个 reviewer，
      每人收到一条 ``task.review_requested`` 事件。第一位沿用旧的
      ``Task.reviewer_id`` CAS 写路径（向后兼容），第 2..N 位插入
      ``review_votes`` 的 NULL verdict 占位行——投票时再落 approve/reject。
      多数决结算逻辑（``_review_vote_counts`` + ``_settle_majority_approved``）
      直接吃这张表，无需改动。
    项目写权限由 project_access_middleware 自动覆盖。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        t = service.assign_task_reviewer(s, tid, user_id=uid, count=count)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
    # The reviewer assignment must be durable before executable review work
    # is delivered to the target Worker.
    s.commit()
    # 事件源：每位 reviewer 独立发一条 task.review_requested；定向走
    # agent_id=其绑定 Agent 队列，无绑定退广播。这样多数决模式下 N 个
    # reviewer 都能从自己的 agent inbox 拿到消息（也兼容 .NET Workflow
    # Consumer 的 broadcast 订阅）。
    # 归属收敛（2026-09-01）：优先用 ReviewVote.reviewer_agent_id 精确定位
    # 被指派的评审 Agent（同 owner 多 agent 时按 user 反查首个会路由错人，
    # 可能路由给实现方自己）；旧数据无 agent 列时回退按 user 反查。
    # T1.1：已指派集合直接按 agent 维度取（review_votes.reviewer_agent_id
    # 是计票事实源），不再「按 user 反查首个 agent」——那会把 fan-out 的
    # 多条消息全路由到同一个 agent（甚至实现方自己）。
    from ...features.scheduling.service import (
        _assigned_task_reviewer_agent_ids,
        publish_workflow_event_for_agent,  # PR-5：resolve worker_id from agent_id
    )
    all_assigned = sorted(_assigned_task_reviewer_agent_ids(s, "task", tid))
    for reviewer_registry_id in all_assigned:
        agent = s.get(service.Agent, reviewer_registry_id)
        if agent is None:
            continue
        reviewer_agent_id = agent.agent_id
        reviewer_user_id = agent.user_id
        # PR-5：走 helper — body 带 agent_id，routing 用 worker_id
        # （每个 reviewer 走自己的 worker queue，多数决 fan-out 不串）
        # PR-10 follow-up：补 agent_type + workload_type="review"。
        # 之前只传 agent_id → .NET WorkflowMessageMapper 看到 agent_type 缺
        # 值 → InvalidDataException → DLQ，reviewer 永远收不到。
        from ..scheduling.service import resolve_agent_executor_type
        reviewer_agent_type = (
            resolve_agent_executor_type(agent, s=s) if agent is not None else ""
        )
        publish_workflow_event_for_agent(
            s, EVENT_TASK_REVIEW_REQUESTED, "task", t.id,
            agent_id=reviewer_agent_id,
            ref_id=reviewer_user_id,
            agent_type=reviewer_agent_type,
            workload_type="review",
        )
        api_helpers._notify_webhooks(s, t.project_id, EVENT_TASK_REVIEW_REQUESTED,
                         {"id": t.id, "reviewer_id": reviewer_user_id,
                          "status": t.status, "fan_out": len(all_assigned)})
    return service._ser(t)



@router.post("/api/tasks/{tid}/review")
def review_task(tid: int, body: AgentReviewIn, authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """Task 评审投票（approve/reject + 评论，CAS）：仅被指派 reviewer 可操作。

    - approve → in_review→done，广播 ``task.reviewed``；
    - reject → review_round+1，退回 in_progress（开发者修复后重新 submit-review），
      达 5 轮上限 → blocked 护栏；广播 ``task.rejected``（ref_id=轮次）。
    项目写权限由 project_access_middleware 自动覆盖。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="review requires login")
    try:
        before = s.get(service.Task, tid)
        before_round = (before.review_round or 0) if before is not None else 0
        owner_agent_id = None
        if before is not None and before.current_assignment_id is not None:
            assignment = s.get(TaskAssignment, before.current_assignment_id)
            if assignment is not None and assignment.agent_registry_id is not None:
                owner = s.get(service.Agent, assignment.agent_registry_id)
                owner_agent_id = owner.agent_id if owner is not None else None
        # T1.1：计票身份下沉到 agent。API key 绑定的 Agent 是权威来源；
        # 拿不到（人类登录 / 旧 key）时由服务层「该 user 唯一 enabled agent」
        # 兜底，仍取不到则 majority 分支 fail closed 拒绝（single 模式不受影响）。
        reviewer_agent_id = None
        try:
            actor = api_helpers.resolve_actor_context(authorization, s)
            reviewer_agent_id = actor.agent_registry_id
        except HTTPException:
            reviewer_agent_id = None
        t = service.review_task(
            s, task_id=tid, reviewer_user_id=uid,
            verdict=body.verdict, comment=body.comment,
            reviewer_agent_id=reviewer_agent_id,
            reviewer_agent_name=api_helpers.resolve_agent_name(authorization, s),
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
    # Persist the verdict before emitting events or dispatching dependencies
    # that were unlocked by it.
    s.commit()
    # 事件源：结算判定（语义同 Story 版）——
    # done → task.reviewed；blocked / round 增加 → task.rejected；
    # 其余（majority 投票未达法定票数）→ review.vote_cast。
    if t.status == Status.DONE:
        event = EVENT_TASK_REVIEWED
    elif t.status == Status.BLOCKED or (t.review_round or 0) > before_round:
        event = EVENT_TASK_REJECTED
    else:
        event = EVENT_TASK_REVIEW_VOTE_CAST
    # ref_id 语义：task.reviewed / task.review_vote_cast → 投票人 uid；
    # task.rejected → 评审轮次
    if event in (EVENT_TASK_REVIEWED, EVENT_TASK_REVIEW_VOTE_CAST):
        ref_id = uid
    else:
        ref_id = t.review_round
    event_kwargs = {"ref_id": ref_id}
    if owner_agent_id and event in (EVENT_TASK_REVIEWED, EVENT_TASK_REJECTED):
        event_kwargs["agent_id"] = owner_agent_id
    publish_workflow_event(event, "task", t.id, **event_kwargs)
    if event == EVENT_TASK_REVIEWED:
        # PR-10：reviewer approve 走 dispatch_implementation_task，server
        # 选 agent + 推 in_progress + publish task.assigned（4 字段齐全）
        # 不再 publish task.available（那是 broadcast 抢任务的旧模式，
        # .NET mapper 看到没 agent_type 会 DLQ）
        try:
            from ..scheduling.service import dispatch_implementation_task
            for successor in service.get_unlocked_dependent_tasks(s, t.id):
                dispatch_implementation_task(s, successor.id)
        except Exception:
            log.exception("reviewer approve: dispatch successor 失败")
        if t.story_id:
            try:
                story_tasks = s.query(service.Task).filter(service.Task.story_id == t.story_id).all()
                if story_tasks and all(tk.status == Status.DONE for tk in story_tasks):
                    from ..projects import service as projects_service
                    projects_service.complete_story(
                        s, t.story_id, changed_by=uid, reason="所有任务完成，Server 自动收尾"
                    )
            except Exception:
                pass
    # Webhook 通道（Epic 122 切片 3）
    api_helpers._notify_webhooks(s, t.project_id, event,
                     {"id": t.id, "status": t.status, "reviewer_id": t.reviewer_id,
                      "review_round": t.review_round})
    return service._ser(t)


@router.delete("/api/tasks/{tid}")
def delete_task(tid: int, s: Session = Depends(get_session)):
    task = service.get_task(s, tid)
    pid = task.project_id if task else None
    if not service.delete_task(s, tid):
        raise HTTPException(status_code=404, detail="task not found")
    if pid:
        api_helpers._invalidate_stats_cache(pid)
    return {"ok": True}


# ---------- 评审统计与超时护栏（Epic 122 S3 M2） ----------

@router.get("/api/review-stats")
def review_stats(project_id: int, days: int = 7, user_id: int | None = None,
                 authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """项目级评审统计运营视图（S3 M2）。

    权限：project_access_middleware 经 ?project_id= 解析项目 → 项目成员可读
    （公开项目读开放 / admin 全局绕过）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        return service.get_review_stats(s, project_id=project_id, days=days,
                                        user_id=user_id)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/review-stats/reassign-timeout")
def reassign_timeout(project_id: int | None = None,
                     body: ReassignTimeoutIn | None = None,
                     authorization: str | None = Header(None),
                     s: Session = Depends(get_session)):
    """评审超时自愈扫描（S3 M2 护栏）：超时 pending_review Story / in_review Task →
    轮次上限 blocked，否则 CAS 解绑重派。

    权限：带 ?project_id= 时由 project_access_middleware 解析 → 项目成员写；
    不带时（Worker 全局自愈扫描）放行 —— 幂等 + 有界 + 不读敏感数据，仅做评审指派
    自愈，任意已认证用户可触发（Worker 以服务账号调用）。
    重派成功的实体发布 review.requested（定向新 reviewer agent 队列退广播）+
    Webhook 通道，与既有 assign-reviewer 端点事件语义一致。
    """
    if body is None:
        body = ReassignTimeoutIn()
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    result = service.scan_review_timeouts(
        s, project_id=project_id,
        timeout_minutes=body.timeout_minutes,
        max_per_run=body.max_per_run)
    # 事件源：重派成功的 Story/Task 逐个发布 entity.review_requested（定向退广播）+ Webhook
    _STORY_REVIEW_EVENTS = {
        "story": EVENT_STORY_REVIEW_REQUESTED,
        "task": EVENT_TASK_REVIEW_REQUESTED,
    }
    for entity_type, rows in (("story", result.get("_stories_reassigned") or []),
                              ("task", result.get("_tasks_reassigned") or [])):
        review_event = _STORY_REVIEW_EVENTS[entity_type]
        for eid, new_reviewer_id in rows:
            reviewer_agent_id = None
            if new_reviewer_id is not None:
                agent = s.query(service.Agent).filter(
                    service.Agent.user_id == new_reviewer_id).first()
                if agent is not None:
                    reviewer_agent_id = agent.agent_id
            # PR-5：走 helper resolve worker_id from agent_id，routing 用
            # worker_id（每个 reviewer 走自己的 worker queue）
            # PR-10 follow-up：补 agent_type + workload_type
            from ..scheduling.service import (
                publish_workflow_event_for_agent, resolve_agent_executor_type,
            )
            publish_workflow_event_for_agent(
                s, review_event, entity_type, eid,
                agent_id=reviewer_agent_id,
                ref_id=new_reviewer_id,
                agent_type=resolve_agent_executor_type(agent, s=s) if agent is not None else "",
                workload_type="review",
            )
            if project_id is not None:
                api_helpers._notify_webhooks(s, project_id, review_event,
                                 {"id": eid, "reviewer_id": new_reviewer_id,
                                  "status": "pending_review" if entity_type == "story" else "in_review"})
    return {k: v for k, v in result.items() if not k.startswith("_")}


# ---------- Bulk Task Operations ----------

@router.post("/api/tasks/bulk-update")
def bulk_update_tasks(body: BulkTaskUpdate, authorization: str | None = Header(None),
                      s: Session = Depends(get_session)):
    """批量更新任务：支持 status / priority / sprint_id / assignee_id / due_date"""
    results = []
    errors = []
    affected_pids = set()
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    for tid in body.task_ids:
        task = service.get_task(s, tid)
        if not task:
            errors.append({"task_id": tid, "error": "task not found"})
            continue
        try:
            updates = {}
            if body.status is not None:
                service.set_status(s, tid, body.status, changed_by=uid, reason="bulk",
                                   status_reason=body.status_reason)
            if body.priority is not None:
                updates["priority"] = body.priority
            if body.sprint_id is not None:
                updates["sprint_id"] = body.sprint_id
            if body.assignee_id is not None:
                updates["assignee_id"] = body.assignee_id
            elif body.clear_assignee:
                updates["assignee_id"] = None
            # v3.2 批量改截止日期：clear_due_date 优先清空；否则按传入 due_date 设置
            if body.clear_due_date:
                updates["due_date"] = None
            elif body.due_date is not None:
                updates["due_date"] = body.due_date
            if updates:
                service.update_task(s, tid, **updates)
            results.append({"task_id": tid, "ok": True})
            affected_pids.add(task.project_id)
        except Exception as e:
            errors.append({"task_id": tid, "error": str(e)})
    for pid in affected_pids:
        api_helpers._invalidate_stats_cache(pid)
    return {"updated": results, "errors": errors}



@router.post("/api/tasks/bulk-delete")
def bulk_delete_tasks(body: BulkTaskDelete, s: Session = Depends(get_session)):
    """批量删除任务"""
    results = []
    errors = []
    affected_pids = set()
    for tid in body.task_ids:
        task = service.get_task(s, tid)
        pid = task.project_id if task else None
        try:
            if service.delete_task(s, tid):
                results.append({"task_id": tid, "ok": True})
                if pid:
                    affected_pids.add(pid)
            else:
                errors.append({"task_id": tid, "error": "task not found"})
        except Exception as e:
            errors.append({"task_id": tid, "error": str(e)})
    for pid in affected_pids:
        api_helpers._invalidate_stats_cache(pid)
    return {"deleted": results, "errors": errors}



@router.post("/api/tasks/{tid}/spec/append")
def append_task_spec(tid: int, body: SpecAppendIn, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.append_task_spec(s, tid, body.text), "task"))


# ---------- Comments ----------

@router.get("/api/tasks/{tid}/comments")
def list_comments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, task_id=tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/tasks/{tid}/comments", status_code=201)
def create_comment(
    tid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        author = api_helpers.resolve_comment_author(authorization, s, body.author)
        comment = service.create_comment(s, task_id=tid, author=author, content=body.content)
        api_helpers._mention_notify(s, author=author, content=body.content, link=f"/task/{tid}")
        return service._ser(comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------- Story / Epic Comments ----------

@router.delete("/api/comments/{cid}")
def delete_comment(cid: int, s: Session = Depends(get_session)):
    if not service.delete_comment(s, cid):
        raise HTTPException(status_code=404, detail="comment not found")
    return {"ok": True}



@router.post("/api/tasks/{tid}/generate-subtasks")
def generate_subtasks(tid: int, s: Session = Depends(get_session)):
    try:
        created = service.generate_tasks_from_spec(s, tid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [service._ser(t) for t in created]


# ---------- Search ----------

@router.get("/api/tasks")
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, sprint_id: int | None = None,
                 type: str | None = None, status: str | None = None,
                 priority: str | None = None, q: str | None = Query(None),
                 reviewer_id: str | None = Query(None),
                 limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                 s: Session = Depends(get_session),
                 authorization: str | None = Header(None)):
    rid: int | None = None
    if reviewer_id:
        if reviewer_id == "me":
            uid, _ = api_helpers._caller_uid_admin(authorization)
            if uid is None:
                raise HTTPException(status_code=422, detail="reviewer_id=me requires login")
            rid = uid
        else:
            try:
                rid = int(reviewer_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="invalid reviewer_id")
    try:
        rows = service.search_tasks(s, project_id=project_id, epic_id=epic_id,
                                    story_id=story_id, sprint_id=sprint_id,
                                    type=type, status=status,
                                    priority=priority, q=q, reviewer_id=rid,
                                    limit=limit, offset=offset)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(t) for t in rows]


# 全局 Story 关键词搜索（命令面板等场景）；路径用 /api/search/stories 避免与 /api/stories/{sid} 冲突

@router.get("/api/tasks/{tid}/attachments")
def list_attachments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(a) for a in service.list_attachments(s, tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/tasks/{tid}/attachments", status_code=201)
async def upload_attachment(tid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="failed to read file")
    try:
        att = service.create_attachment(s, task_id=tid, content=content,
                                         original_name=file.filename or "unnamed",
                                         mime_type=file.content_type or "application/octet-stream")
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(att)



@router.get("/api/attachments/{aid}")
def download_attachment(aid: int, s: Session = Depends(get_session)):
    att = service.get_attachment(s, aid)
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    path = service.get_attachment_path(att)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found on disk")
    return FileResponse(path, media_type=att.mime_type, filename=att.original_name)



@router.get("/api/attachments/{aid}/info")
def attachment_info(aid: int, s: Session = Depends(get_session)):
    att = service.get_attachment(s, aid)
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    return service._ser(att)



@router.delete("/api/attachments/{aid}")
def delete_attachment(aid: int, s: Session = Depends(get_session)):
    if not service.delete_attachment(s, aid):
        raise HTTPException(status_code=404, detail="attachment not found")
    return {"ok": True}


# ---------- COS 图片上传（Epic 64 S1） ----------
_COS_MAX_SIZE = 10 * 1024 * 1024  # 10MB
_COS_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_COS_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}



@router.post("/api/tasks/{tid}/dependencies", status_code=201)
def add_dependency(
    tid: int,
    depends_on_id: int = Query(..., description="被依赖的任务 ID"),
    dependency_type: str = Query("blocks", pattern=r"^(blocks|blocked_by|relates_to)$"),
    s: Session = Depends(get_session),
):
    """添加任务依赖关系。"""
    try:
        dep = service.add_task_dependency(
            s, task_id=tid, depends_on_id=depends_on_id, dependency_type=dependency_type,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(dep)



@router.get("/api/tasks/{tid}/dependencies")
def get_dependencies(tid: int, s: Session = Depends(get_session)):
    """获取任务的依赖关系（blockers 和 blocked_by）。"""
    try:
        return service.get_task_dependencies(s, tid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/tasks/{tid}/review-context")
def get_task_review_context(
    tid: int,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    # P0-1: review-context returns task + parent proposal spec; project
    # membership must be enforced or any authenticated caller could read
    # another project's proposal content via the task id alone.
    api_helpers._authorize_task_read(authorization, s, tid)
    task = service.get_task(s, tid)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    assignment = (
        s.get(TaskAssignment, task.current_assignment_id)
        if task.current_assignment_id is not None else None
    )
    owner_agent = (
        s.get(service.Agent, assignment.agent_registry_id)
        if assignment is not None and assignment.agent_registry_id is not None
        else None
    )
    # 获取任务及附属 PR Diff
    context = {
        "task": service._ser(task),
        "comments": [service._ser(c) for c in service.list_comments(s, task_id=tid)],
        "review_round": task.review_round,
        "owner_agent_id": owner_agent.agent_id if owner_agent is not None else None,
        "pr_diff": None,
        "pr_diff_available": False,
        "pr_diff_source": None,
    }
    # 向上追溯 Proposal Specs
    if task.story_id:
        from ..projects.models import Story, Epic
        from ..proposals.models import Proposal, ProposalTicketRequest
        story = s.get(Story, task.story_id)
        if story:
            epic = s.get(Epic, story.epic_id)
            proposal_id = getattr(epic, "proposal_id", None) if epic else None
            if proposal_id is None and epic is not None:
                request = s.query(ProposalTicketRequest).filter(
                    ProposalTicketRequest.parent_story_id == story.id,
                ).order_by(ProposalTicketRequest.id.desc()).first()
                if request is None:
                    request = s.query(ProposalTicketRequest).filter(
                        ProposalTicketRequest.parent_epic_id == epic.id,
                    ).order_by(ProposalTicketRequest.id.desc()).first()
                proposal_id = request.proposal_id if request is not None else None
            if proposal_id is not None:
                proposal = s.get(Proposal, proposal_id)
                if proposal:
                    context["proposal_spec"] = proposal.converged_spec or proposal.content
    return context
