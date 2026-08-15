"""Projects feature router (Phase 5 split from api.py)。

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
import uuid
from ...cache import get_cache
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.
from ... import mq  # publish_workflow_event + EVENT_* constants
from ...mq import (
    EVENT_STORY_CREATED, EVENT_STORY_CONFIRMED, EVENT_STORY_READY,
    EVENT_STORY_REVIEW_REQUESTED, EVENT_STORY_REVIEW_REJECTED,
    EVENT_STORY_COMMENT_REPLIED,
    EVENT_TASK_AVAILABLE, EVENT_TASK_ASSIGNED,
    EVENT_TASK_READY_FOR_REVIEW, EVENT_TASK_REVIEWED, EVENT_TASK_REJECTED,
    publish_workflow_event,
)

router = APIRouter(tags=["projects"])


@router.get("/api/projects")
def list_projects_ext(
    s: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    """列表 API：admin 可见全部项目；普通用户仅见受邀（成员）项目。

    访问权限由 ``list_accessible_projects`` 中 ``user.is_admin`` 控制。
    API Key（``abk_``）的身份解析完全等同于其关联用户 —— 若 key 属于非管理
    员，则仅返回该用户的成员项目；若属于管理员，则可见全部。

    Story 137：默认隐藏已归档项目（``is_archived=True``），传 ``include_archived=true`` 才会包含。
    """
    uid = api_helpers._optional_user_id(authorization, s)
    projects, total = service.list_accessible_projects(s, uid, limit=limit, offset=offset)
    return {"items": [service._ser(p) for p in projects], "total": total}


# ---- Story 137：项目中心 ----
@router.get("/api/projects/center")
def list_projects_center(
    s: Session = Depends(get_session),
    scope: str = Query("active", pattern=r"^(active|archived|all|mine|created)$"),
    sort: str = Query("recent", pattern=r"^(recent|name|created|tasks)$"),
    include_archived: bool | None = Query(None, description="scope=all 时是否含已归档；None/true=含"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    """项目中心专用列表：带筛选 / 排序 / 任务/成员/活跃统计字段。

    返回 ``items`` 中每项是 Project 列 + 附加：
    - task_count / task_done
    - member_count
    - last_activity_at（ISO 8601；可能为 null）
    """
    uid = api_helpers._optional_user_id(authorization, s)
    items, total = service.list_accessible_projects_center(
        s, uid, scope=scope, sort=sort,
        include_archived=include_archived, limit=limit, offset=offset,
    )
    return {"items": items, "total": total}


@router.post("/api/projects/bulk-archive")
def bulk_archive_projects(
    body: dict,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """批量归档。body = ``{"ids": [1, 2, 3]}``。需要每个项目 owner 权限。"""
    uid = api_helpers._optional_user_id(authorization, s)
    if uid is None:
        raise HTTPException(status_code=401, detail="authentication required")
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=422, detail="ids must be a list of integers")
    # 权限：仅允许归档自己有 owner 权限的项目
    user = service.get_user(s, uid)
    if not user:
        raise HTTPException(status_code=401, detail="user not found")
    if not user.is_admin:
        allowed_ids = {
            r[0] for r in s.query(ProjectMember.project_id)
            .filter(ProjectMember.user_id == uid, ProjectMember.role == "owner")
            .all()
        }
        denied = [i for i in ids if i not in allowed_ids]
        if denied:
            raise HTTPException(
                status_code=403,
                detail=f"owner permission required for projects: {denied}",
            )
    affected = service.bulk_archive(s, [int(i) for i in ids], user_id=uid)
    return {"ok": True, "archived": affected}


@router.post("/api/projects/bulk-unarchive")
def bulk_unarchive_projects(
    body: dict,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """批量恢复归档。权限同 bulk-archive。"""
    uid = api_helpers._optional_user_id(authorization, s)
    if uid is None:
        raise HTTPException(status_code=401, detail="authentication required")
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=422, detail="ids must be a list of integers")
    user = service.get_user(s, uid)
    if not user:
        raise HTTPException(status_code=401, detail="user not found")
    if not user.is_admin:
        allowed_ids = {
            r[0] for r in s.query(ProjectMember.project_id)
            .filter(ProjectMember.user_id == uid, ProjectMember.role == "owner")
            .all()
        }
        denied = [i for i in ids if i not in allowed_ids]
        if denied:
            raise HTTPException(
                status_code=403,
                detail=f"owner permission required for projects: {denied}",
            )
    affected = service.bulk_unarchive(s, [int(i) for i in ids])
    return {"ok": True, "unarchived": affected}



@router.post("/api/projects", status_code=201)
def create_project(
    body: ProjectIn, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    user = api_helpers._current_user(authorization, s, required_permission="api:write") if authorization or api_helpers._auth_is_required() else None
    p = service.create_project(s, name=body.name, key=body.key, description=body.description)
    # 创建者自动成为项目 owner；本地显式开放模式仍兼容匿名项目。
    uid = user.id if user else None
    if uid:
        service.add_project_member(s, project_id=p.id, user_id=uid, role="owner")
    return service._ser(p)



@router.get("/api/projects/{pid}")
def get_project_ext(
    pid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """获取项目：admin 可见全部，普通用户仅可见其成员项目（邀请制）"""
    p = api_helpers._need(service.get_project(s, pid), "project")
    uid = api_helpers._optional_user_id(authorization, s)
    user = service.get_user(s, uid) if uid else None
    if not (user and user.is_admin) and not service.user_is_project_member(s, pid, uid):
        raise HTTPException(status_code=403, detail="access denied: project membership required")
    return service._ser(p)



@router.patch("/api/projects/{pid}")
def update_project(
    pid: int, body: ProjectPatchExtended, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    api_helpers._need(service.get_project(s, pid), "project")
    api_helpers._require_project_owner(s, pid, authorization)
    r = service.update_project(s, pid, **body.model_dump(exclude_none=True))
    return service._ser(api_helpers._need(r, "project"))



@router.delete("/api/projects/{pid}")
def delete_project(
    pid: int, authorization: str | None = Header(None), s: Session = Depends(get_session),
):
    api_helpers._need(service.get_project(s, pid), "project")
    api_helpers._require_project_owner(s, pid, authorization)
    if not service.delete_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


# ---------- Epics ----------

@router.get("/api/projects/{pid}/epics")
def list_epics(pid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0)):
    return [service._ser(e) for e in service.list_epics(s, pid, limit=limit, offset=offset)]



@router.get("/api/projects/{pid}/kanban")
def list_project_kanban(pid: int, include_all: bool = Query(False),
                        s: Session = Depends(get_session)):
    """项目看板（Epic 130）：默认只看 in_kanban 标记的 Story，含其下 task 状态。"""
    api_helpers._need(service.get_project(s, pid), "project")
    return service.list_project_kanban(s, pid, include_all=include_all)



@router.post("/api/projects/{pid}/epics", status_code=201)
def create_epic(pid: int, body: EpicIn, s: Session = Depends(get_session)):
    api_helpers._need(service.get_project(s, pid), "project")
    return service._ser(service.create_epic(s, project_id=pid, title=body.title, description=body.description))



@router.get("/api/epics/{eid}")
def get_epic(eid: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_epic(s, eid), "epic"))



@router.patch("/api/epics/{eid}")
def update_epic(eid: int, body: EpicPatch, s: Session = Depends(get_session)):
    r = service.update_epic(s, eid, **body.model_dump(exclude_none=True))
    return service._ser(api_helpers._need(r, "epic"))



@router.delete("/api/epics/{eid}")
def delete_epic(eid: int, s: Session = Depends(get_session)):
    if not service.delete_epic(s, eid):
        raise HTTPException(status_code=404, detail="epic not found")
    return {"ok": True}


# ---------- Stories ----------

@router.get("/api/epics/{eid}/stories")
def list_stories(eid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
                 offset: int = Query(0, ge=0)):
    return [service._ser(x) for x in service.list_stories(s, eid, limit=limit, offset=offset)]



@router.post("/api/epics/{eid}/stories", status_code=201)
def create_story(eid: int, body: StoryIn, s: Session = Depends(get_session)):
    epic = api_helpers._need(service.get_epic(s, eid), "epic")
    st = service.create_story(s, epic_id=eid, title=body.title,
                              description=body.description, needs_design=body.needs_design)
    # 事件源：Story 创建广播（分配器 worker 消费后自动指派 reviewer）
    publish_workflow_event(EVENT_STORY_CREATED, "story", st.id, ref_id=eid)
    # Webhook 通道（Epic 122 切片 3）：面向外部系统/常驻 Runner
    api_helpers._notify_webhooks(s, epic.project_id, EVENT_STORY_CREATED,
                     {"id": st.id, "epic_id": eid, "title": st.title, "status": st.status})
    return service._ser(st)



@router.get("/api/stories/{sid}")
def get_story(sid: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_story(s, sid), "story"))



@router.patch("/api/stories/{sid}")
def update_story(sid: int, body: StoryPatch, s: Session = Depends(get_session)):
    """更新 Story；in_kanban 置 True 时联动确认（backlog→confirmed）+ 广播任务。"""
    payload = body.model_dump(exclude_none=True)
    r = service.update_story(s, sid, **payload)
    st = api_helpers._need(r, "story")
    # Epic 130: ticket 标记「进入 kanban」→ 自动触发 Agent 编排
    if payload.get("in_kanban") is True and st.status in ("backlog", "confirmed"):
        try:
            if st.status == "backlog":
                service.confirm_story(s, st.id)
                st = service.get_story(s, st.id) or st
                publish_workflow_event(EVENT_STORY_CONFIRMED, "story", st.id,
                                       ref_id=st.epic_id)
            for t in service.list_tasks(s, story_id=sid, limit=200):
                if t.status in ("backlog", "todo"):
                    publish_workflow_event(EVENT_TASK_AVAILABLE, "task", t.id,
                                           ref_id=sid)
        except Exception:
            log.exception("看板标记后广播任务失败（不影响标记本身）")
    return service._ser(st)



@router.post("/api/stories/{sid}/confirm")
def confirm_story(sid: int, authorization: str | None = Header(None),
                  s: Session = Depends(get_session)):
    """用户确认 Story 开始（Ticket 全流程人工闸门）：backlog → confirmed。

    确认后发 MQ ``story.confirmed`` 触发 agent 自动处理编排（切片 2 由
    Proposal Worker 轮询拉起 agent）。CAS 幂等：已 confirmed 直接返回。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    st = service.confirm_story(s, sid, changed_by=uid)
    publish_workflow_event(EVENT_STORY_CONFIRMED, "story", st.id, ref_id=st.epic_id)
    # Agent MQ 编排（2026-08-09）：广播其下 backlog/todo 任务 → 各 agent worker
    # 竞争认领（task.available + 服务端 claim CAS）。MQ 未配置时 no-op，
    # 由 worker 的 story 扫描轮询兜底（handle_story 竞争认领）。
    try:
        for t in service.list_tasks(s, story_id=sid, limit=200):
            if t.status in ("backlog", "todo"):
                publish_workflow_event(EVENT_TASK_AVAILABLE, "task", t.id,
                                       ref_id=sid)
    except Exception:
        log.exception("confirm 广播 task.available 失败（不影响主流程）")
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        api_helpers._notify_webhooks(s, _epic.project_id, EVENT_STORY_CONFIRMED,
                         {"id": st.id, "epic_id": st.epic_id, "status": st.status})
    return service._ser(st)



@router.get("/api/stories/{sid}/status-history")
def story_status_history(sid: int, limit: int = Query(100, ge=1, le=500),
                         s: Session = Depends(get_session)):
    """Story 状态变更历史（Ticket 全流程），按时间倒序。"""
    api_helpers._need(service.get_story(s, sid), "story")
    rows = service.list_story_status_history(s, sid, limit=limit)
    return {"items": [service._ser(x) for x in rows], "total": len(rows)}



@router.post("/api/stories/{sid}/complete")
def complete_story(sid: int, authorization: str | None = Header(None),
                   s: Session = Depends(get_session)):
    """Story 自动收尾（Ticket 全流程）：任意非 done/blocked → done。

    Worker 在 Story 下全部 task 完成后调用（agent 自动处理收尾）；blocked
    拒绝（人工仲裁态）。幂等：已 done 直接返回。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    st = service.complete_story(s, sid, changed_by=uid, reason="worker 自动收尾")
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        api_helpers._notify_webhooks(s, _epic.project_id, "story.completed",
                         {"id": st.id, "status": st.status})
    return service._ser(st)



@router.post("/api/stories/{sid}/claim")
def claim_story(sid: int, authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """Worker 竞争认领 Story（Ticket 全流程多实例编排）：CAS confirmed → todo。

    多 Worker 实例（不同 agent CLI）竞争同一 confirmed Story 时恰一赢家；
    竞争失败返回 409（已被其它实例认领 / 状态不可认领）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    try:
        st = service.claim_story(s, sid, changed_by=uid)
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(st)



@router.post("/api/stories/{sid}/unclaim")
def unclaim_story(sid: int, authorization: str | None = Header(None),
                  s: Session = Depends(get_session)):
    """Worker 认领交接/失败回退（Ticket 全流程）：CAS todo → confirmed。

    agent 本轮未完成全部任务或失败时回退 confirmed 重新入池；blocked 拒绝。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    try:
        st = service.unclaim_story(s, sid, changed_by=uid, reason="worker 交接/失败回退")
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(st)



@router.delete("/api/stories/{sid}")
def delete_story(sid: int, s: Session = Depends(get_session)):
    if not service.delete_story(s, sid):
        raise HTTPException(status_code=404, detail="story not found")
    return {"ok": True}


# ---------- Story 评审闭环（Epic 122 S1） ----------

@router.get("/api/stories")
def list_stories_global(status: str | None = Query(None), reviewer_id: str | None = Query(None),
                        project_id: int | None = Query(None), limit: int = Query(100, ge=1, le=200),
                        offset: int = Query(0, ge=0),
                        authorization: str | None = Header(None),
                        s: Session = Depends(get_session)):
    """全局 Story 列表（供评审任务拉取等场景）。

    - ``?reviewer_id=me`` 解析为当前登录用户，返回指派给我的评审任务；
    - ``?status=pending_review`` 按评审态过滤；
    - ``?project_id=N`` 限定项目（配合 project_access_middleware 权限）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    q = s.query(service.Story)
    if status:
        if status not in service.STORY_STATUSES:
            raise HTTPException(status_code=422, detail=f"invalid status '{status}'")
        q = q.filter(service.Story.status == status)
    if reviewer_id:
        if reviewer_id == "me":
            if api_helpers._auth_is_required() and uid is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            if uid is None:
                raise HTTPException(status_code=422, detail="reviewer_id=me requires login")
            q = q.filter(service.Story.reviewer_id == uid)
        else:
            try:
                q = q.filter(service.Story.reviewer_id == int(reviewer_id))
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid reviewer_id")
    if project_id is not None:
        q = q.join(service.Epic, service.Story.epic_id == service.Epic.id).filter(
            service.Epic.project_id == project_id
        )
    total = q.count()
    items = [service._ser(x) for x in q.order_by(service.Story.id.desc()).limit(limit).offset(offset).all()]
    return {"items": items, "total": total}



@router.post("/api/stories/{sid}/assign-reviewer")
def assign_story_reviewer(sid: int, authorization: str | None = Header(None),
                          s: Session = Depends(get_session)):
    """随机指派评审人（幂等；CAS 并发安全）。项目成员写权限由中间件覆盖。"""
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    st = service.assign_reviewer(s, sid, user_id=uid)
    # 事件源：指派成功 → review.requested 定向投递给 reviewer 的 Agent 队列
    # （reviewer 是 users.id；找到其绑定的 Agent 才能定向，否则退化为广播）
    reviewer_agent_id = None
    if st.reviewer_id is not None:
        agent = s.query(service.Agent).filter(service.Agent.user_id == st.reviewer_id).first()
        if agent is not None:
            reviewer_agent_id = agent.agent_id
    publish_workflow_event(EVENT_STORY_REVIEW_REQUESTED, "story", st.id,
                           ref_id=st.reviewer_id, agent_id=reviewer_agent_id)
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        api_helpers._notify_webhooks(s, _epic.project_id, EVENT_STORY_REVIEW_REQUESTED,
                         {"id": st.id, "reviewer_id": st.reviewer_id, "status": st.status})
    return service._ser(st)



@router.post("/api/stories/{sid}/review")
def review_story(sid: int, body: AgentReviewIn, authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """评审投票（approve/reject + 评论，CAS）：仅被指派 reviewer 可操作。"""
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if uid is None:
        raise HTTPException(status_code=422, detail="review requires login")
    before = s.get(service.Story, sid)
    before_round = (before.review_round or 0) if before is not None else 0
    st = service.review_story(s, story_id=sid, reviewer_user_id=uid,
                              verdict=body.verdict, comment=body.comment)
    # 事件源：结算判定 ——
    # ready → story.ready（评审通过，开发分配入口）；
    # blocked / round 增加 → review.rejected（护栏终态 / single reject /
    #   majority 多数驳回待收敛）；
    # 其余（majority 投票未达法定票数，状态与轮次均未变）→ review.vote_cast。
    if st.status == "ready":
        event = EVENT_STORY_READY
    elif st.status == "blocked" or (st.review_round or 0) > before_round:
        event = EVENT_STORY_REVIEW_REJECTED
    else:
        event = EVENT_STORY_REVIEW_VOTE_CAST
    if event == EVENT_STORY_READY:
        ref_id = st.reviewer_id
    elif event == EVENT_STORY_REVIEW_VOTE_CAST:
        ref_id = uid
    else:
        ref_id = st.review_round
    publish_workflow_event(event, "story", st.id, ref_id=ref_id)
    # Webhook 通道（Epic 122 切片 3）
    _epic = s.get(service.Epic, st.epic_id)
    if _epic is not None:
        api_helpers._notify_webhooks(s, _epic.project_id, event,
                         {"id": st.id, "status": st.status, "reviewer_id": st.reviewer_id,
                          "review_round": st.review_round})
    return service._ser(st)


# ---------- Agents（Epic 122 S1 + 2026-08-09 配置中心化） ----------

@router.get("/api/stories/{sid}/tasks")
def list_tasks(sid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0), sprint_id: int | None = Query(None)):
    q_base = service.query_task_count(s, sid, sprint_id=sprint_id)
    total = q_base
    items = [service._ser(t) for t in service.list_tasks(s, sid, sprint_id=sprint_id, limit=limit, offset=offset)]
    return {"items": items, "total": total}



@router.post("/api/stories/{sid}/tasks", status_code=201)
def create_task(
    sid: int, body: TaskIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    story = api_helpers._need(service.get_story(s, sid), "story")
    try:
        t = service.create_task(s, project_id=body.project_id, story_id=story.id,
                                title=body.title, type=body.type,
                                description=body.description, spec=body.spec,
                                priority=body.priority,
                                assignee_id=body.assignee_id,
                                due_date=body.due_date,
                                labels=body.labels,
                                estimate=body.estimate)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(body.project_id)
    if t.assignee_id is not None:
        service.create_notification(
            s, user_id=t.assignee_id, notif_type="task_assigned",
            title=f"任务 #{t.id} 已分配给你", content=t.title, link=f"/task/{t.id}",
        )
    return service._ser(t)


# ---------- Enhanced Search (must be before /api/tasks/{tid}) ----------

@router.get("/api/stories/{sid}/comments")
def list_story_comments(sid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, story_id=sid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/stories/{sid}/comments", status_code=201)
def create_story_comment(
    sid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        comment = service.create_comment(s, story_id=sid, author=body.author, content=body.content)
        api_helpers._mention_notify(s, author=body.author, content=body.content, link=f"/story/{sid}")
        # 事件源：评审往返收敛 —— 评论者非 reviewer 时定向通知 reviewer；
        # 评论者即 reviewer（评审意见）时退化为广播，作者侧消费者感知。
        st = service.get_story(s, sid)
        reviewer_agent_id = None
        if st is not None and st.reviewer_id is not None:
            agent = s.query(service.Agent).filter(service.Agent.user_id == st.reviewer_id).first()
            reviewer_agent_id = agent.agent_id if agent is not None else None
        publish_workflow_event(EVENT_STORY_COMMENT_REPLIED, "story", sid,
                               ref_id=comment.id, agent_id=reviewer_agent_id)
        # Webhook 通道（Epic 122 切片 3）
        if st is not None:
            _epic = s.get(service.Epic, st.epic_id)
            if _epic is not None:
                api_helpers._notify_webhooks(s, _epic.project_id, EVENT_STORY_COMMENT_REPLIED,
                                 {"id": sid, "comment_id": comment.id, "by": body.author})
        return service._ser(comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))



@router.get("/api/epics/{eid}/comments")
def list_epic_comments(eid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, epic_id=eid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/epics/{eid}/comments", status_code=201)
def create_epic_comment(
    eid: int, body: CommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    try:
        comment = service.create_comment(s, epic_id=eid, author=body.author, content=body.content)
        api_helpers._mention_notify(s, author=body.author, content=body.content, link=f"/epic/{eid}")
        return service._ser(comment)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))



@router.get("/api/projects/{pid}/sprints")
def list_sprints(pid: int, s: Session = Depends(get_session),
                limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    api_helpers._need(service.get_project(s, pid), "project")
    return [service._ser(sp) for sp in service.list_sprints(s, pid, limit=limit, offset=offset)]



@router.post("/api/projects/{pid}/sprints", status_code=201)
def create_sprint(pid: int, body: SprintIn, s: Session = Depends(get_session)):
    api_helpers._need(service.get_project(s, pid), "project")
    return service._ser(service.create_sprint(
        s, project_id=pid, title=body.title, goal=body.goal,
        start_date=body.start_date, end_date=body.end_date))



@router.get("/api/projects/{pid}/cos/config")
def cos_config(pid: int, s: Session = Depends(get_session)):
    """COS 配置状态（前端据此显示上传入口/降级提示）。未配置不报错，返回 configured:false。"""
    if not service.get_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    cfg = _cos_client.config_dict()
    cfg["upload_endpoint"] = f"/api/projects/{pid}/cos/upload"
    return cfg



@router.post("/api/projects/{pid}/cos/upload", status_code=201)
async def cos_upload(pid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    """服务端直传图片至腾讯云 COS，返回预签名 GET URL（24h）供 markdown 引用。

    优雅降级：COS 环境变量未配置时返回 503 明确错误，不阻断其他功能。
    权限：路由 /api/projects/{pid}/... 由 project_access_middleware 自动覆盖（成员写/管理员绕过）。
    """
    if not service.get_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    if not _cos_client.is_configured():
        raise HTTPException(status_code=503,
                            detail=f"COS not configured: {_cos_client.config_error} (set COS_SECRET_ID/COS_SECRET_KEY/COS_BUCKET/COS_REGION)")
    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="failed to read file")
    if len(content) > _COS_MAX_SIZE:
        raise HTTPException(status_code=422, detail="file too large (max 10MB)")
    mime = file.content_type or "application/octet-stream"
    if mime not in _COS_ALLOWED_TYPES:
        raise HTTPException(status_code=422,
                            detail=f"unsupported content type: {mime} (allowed: image/png, image/jpeg, image/gif, image/webp)")
    original_name = file.filename or "unnamed"
    ext = os.path.splitext(original_name)[1].lower() or api_helpers._ext_for_mime(mime)
    if ext not in _COS_ALLOWED_EXTS:
        ext = api_helpers._ext_for_mime(mime)
    key = f"uploads/{pid}/{uuid.uuid4().hex}{ext}"
    try:
        _cos_client.put_object(key, content, content_type=mime)
    except CosError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "key": key,
        "url": _cos_client.presigned_get_url(key),
        "size": len(content),
        "content_type": mime,
        "original_name": original_name,
        "cos_configured": True,
    }


# ---------- AgentSchedule ----------

@router.get("/api/projects/{pid}/schedules")
def list_schedules(pid: int, s: Session = Depends(get_session),
                   limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    api_helpers._need(service.get_project(s, pid), "project")
    return [service._ser(sch) for sch in service.list_schedules(s, pid, limit=limit, offset=offset)]



@router.post("/api/projects/{pid}/schedules", status_code=201)
def create_schedule(pid: int, body: ScheduleIn, s: Session = Depends(get_session)):
    api_helpers._need(service.get_project(s, pid), "project")
    try:
        sch = service.create_schedule(
            s, project_id=pid, title=body.title,
            schedule_type=body.schedule_type, cron_expr=body.cron_expr,
            agent=body.agent, task_id=body.task_id,
            task_priority=body.task_priority, task_type=body.task_type,
            epic_id=body.epic_id,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(sch)



@router.get("/api/projects/{pid}/members")
def list_members(
    pid: int, s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    api_helpers._need(service.get_project(s, pid), "project")
    members, total = service.list_project_members(s, pid, limit=limit, offset=offset)
    return {
        "items": [
            {
                **service._ser(m),
                "username": (
                    service.get_user(s, m.user_id).username
                    if service.get_user(s, m.user_id) else None
                ),
            }
            for m in members
        ],
        "total": total,
    }



@router.post("/api/projects/{pid}/members", status_code=201)
def add_member(
    pid: int, body: dict,
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """邀请用户加入项目（仅 owner 或管理员可操作）"""
    uid = api_helpers._current_user(authorization, s, required_permission="api:write").id
    if not service.user_is_project_owner(s, pid, uid):
        u = service.get_user(s, uid) if uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can add members")
    try:
        found_user = service.get_user_by_username(s, body.get("username")) if body.get("username") else None
        user_id = body.get("user_id") or (found_user.id if found_user else None)
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id or username required")
        pm = service.add_project_member(s, project_id=pid, user_id=user_id, role=body.get("role", "member"))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 发送邀请通知
    project = service.get_project(s, pid)
    service.create_notification(
        s, user_id=user_id, notif_type="project_invite",
        title=f"项目邀请：{project.name}",
        content=f"你已被邀请加入项目「{project.name}」（{project.key or ''}），角色：{body.get('role', 'member')}。",
        link=f"/project/{pid}",
    )
    return service._ser(pm)



@router.delete("/api/projects/{pid}/members/{uid}")
def remove_member(
    pid: int, uid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """移除项目成员（仅 owner 或管理员可操作，owner 不能移除自己）"""
    current_uid = api_helpers._current_user(authorization, s, required_permission="api:write").id
    if not service.user_is_project_owner(s, pid, current_uid):
        u = service.get_user(s, current_uid) if current_uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can remove members")
    try:
        if not service.remove_project_member(s, pid, uid):
            raise HTTPException(status_code=404, detail="member not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}



@router.patch("/api/projects/{pid}/members/{uid}")
def update_member_role(
    pid: int, uid: int, body: MemberRoleIn, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """更新成员角色（仅 owner 或管理员可操作）"""
    current_uid = api_helpers._current_user(authorization, s, required_permission="api:write").id
    if not service.user_is_project_owner(s, pid, current_uid):
        u = service.get_user(s, current_uid) if current_uid else None
        if not (u and u.is_admin):
            raise HTTPException(status_code=403, detail="only owner or admin can update member role")
    try:
        pm = service.update_project_member_role(s, pid, uid, body.role)
        if not pm:
            raise HTTPException(status_code=404, detail="member not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(pm)


# ---------- Notifications ----------

@router.get("/api/projects/{pid}/stats")
def project_stats(pid: int, s: Session = Depends(get_session)):
    cache = get_cache()
    cache_key = f"stats:{pid}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    api_helpers._need(service.get_project(s, pid), "project")
    result = service.get_project_stats(s, pid)
    cache.set(cache_key, result, _CACHE_TTL_STATS)
    return result


# ---------- Dashboard overview（跨项目聚合统计，首页性能优化） ----------

@router.get("/api/projects/{pid}/export")
def export_project(
    pid: int, format: str = Query("json", pattern=r"^(json)$"),
    s: Session = Depends(get_session),
):
    """导出项目完整数据为 JSON。"""
    try:
        return service.export_project_data(s, pid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.get("/api/stories/{sid}/export")
def export_story(
    sid: int, format: str = Query("json", pattern=r"^(json)$"),
    s: Session = Depends(get_session),
):
    """导出 Story 及所有子任务为 JSON。"""
    try:
        return service.export_story_data(s, sid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- Epic 22 Story 22.1: 审计日志 ----------

@router.post("/api/projects/{pid}/import")
def import_tasks(
    pid: int,
    body: dict,
    s: Session = Depends(get_session),
):
    """从 JSON 数据批量导入任务。"""
    try:
        return service.import_tasks_from_json(s, pid, body)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------- Epic 22 Story 22.4: Webhook 配置 ----------
