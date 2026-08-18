"""Work_Items feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ... import service
from agentboard.schemas import *  # Phase 5: forward-ref-safe
import os
from ...models import Status
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.
from ... import mq  # publish_workflow_event + EVENT_* constants
from ...mq import (
    EVENT_TASK_ASSIGNED, EVENT_TASK_READY_FOR_REVIEW, EVENT_TASK_REVIEWED,
    EVENT_TASK_REJECTED, EVENT_TASK_REVIEW_REQUESTED, EVENT_TASK_REVIEW_VOTE_CAST,
    EVENT_STORY_REVIEW_REQUESTED,
    publish_workflow_event,
)

router = APIRouter(tags=["work_items"])


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
def get_task(tid: int, s: Session = Depends(get_session)):
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
            publish_workflow_event(EVENT_TASK_ASSIGNED, "task", updated.id,
                                   ref_id=updated.story_id,
                                   agent_id=_agent.agent_id)
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



@router.get("/api/tasks/{tid}/status-history")
def get_task_status_history(tid: int, authorization: str | None = Header(None),
                            s: Session = Depends(get_session)):
    """任务状态变更历史（Epic 123）：from_status → to_status、操作人、原因、时间，倒序。"""
    api_helpers._need(service.get_task(s, tid), "task")
    return [service._ser(h) for h in service.list_task_status_history(s, tid)]



@router.post("/api/tasks/{tid}/claim")
def claim_task_for_development(tid: int, authorization: str | None = Header(None),
                               s: Session = Depends(get_session)):
    """开发任务竞争认领（Epic 122 切片 2，CAS 并发安全）。

    条件 UPDATE ``status IN (todo,)``（Story 265 backlog 已下线）→ ``in_progress + assignee_id=当前用户``，
    rowcount=1 才成功；已认领/已结束返回 409 明确错误（复用 Epic 118 护栏语义）。
    项目写权限由 project_access_middleware 自动覆盖。
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
        publish_workflow_event(
            EVENT_TASK_ASSIGNED,
            "task",
            assigned_task.id,
            ref_id=assigned_task.story_id,
            agent_id=agent.agent_id,
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
    # 事件源：任务进入评审态 → 广播（消息只带定位信息，状态回查 DB）
    publish_workflow_event(EVENT_TASK_READY_FOR_REVIEW, "task", t.id,
                           ref_id=t.assignee_id)
    api_helpers._notify_webhooks(s, t.project_id, EVENT_TASK_READY_FOR_REVIEW,
                     {"id": t.id, "assignee_id": t.assignee_id, "status": t.status})
    return service._ser(t)



@router.post("/api/tasks/{tid}/assign-reviewer")
def assign_task_reviewer(tid: int, authorization: str | None = Header(None),
                         s: Session = Depends(get_session)):
    """随机指派 Task 评审人（幂等，CAS 并发安全，Epic 122 切片 2 M2）。

    - 候选 = 在线 reviewer ∩ 项目成员 ∩ ≠ assignee；无候选 → 422；
    - 成功 → 定向投递 review.requested（entity_type=task）给 reviewer 绑定的
      Agent 队列（无 Agent 绑定退化为广播，开发者轮询 list_review_tasks 兜底）。
    项目写权限由 project_access_middleware 自动覆盖。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        t = service.assign_task_reviewer(s, tid, user_id=uid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
    # 事件源：指派成功 → review.requested（定向 reviewer agent；无绑定退广播）
    reviewer_agent_id = None
    if t.reviewer_id is not None:
        agent = s.query(service.Agent).filter(service.Agent.user_id == t.reviewer_id).first()
        if agent is not None:
            reviewer_agent_id = agent.agent_id
    publish_workflow_event(EVENT_TASK_REVIEW_REQUESTED, "task", t.id,
                           ref_id=t.reviewer_id, agent_id=reviewer_agent_id)
    api_helpers._notify_webhooks(s, t.project_id, EVENT_TASK_REVIEW_REQUESTED,
                     {"id": t.id, "reviewer_id": t.reviewer_id, "status": t.status})
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
        t = service.review_task(s, task_id=tid, reviewer_user_id=uid,
                                verdict=body.verdict, comment=body.comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(t.project_id)
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
    publish_workflow_event(event, "task", t.id, ref_id=ref_id)
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
            publish_workflow_event(review_event, entity_type, eid,
                                   ref_id=new_reviewer_id, agent_id=reviewer_agent_id)
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
        comment = service.create_comment(s, task_id=tid, author=body.author, content=body.content)
        api_helpers._mention_notify(s, author=body.author, content=body.content, link=f"/task/{tid}")
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
