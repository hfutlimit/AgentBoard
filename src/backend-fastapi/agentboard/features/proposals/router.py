"""Proposals feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ...core.application import service
from .schemas import (
	ProposalAnswerIn,
	ProposalAskIn,
	ProposalClaimIn,
	ProposalConvertIn,
	ProposalIn,
	ProposalPatch,
	ProposalReclaimIn,
	ProposalStatusIn,
	RecoverFailedIn,
	TicketFailIn,
	TicketReclaimIn,
	TicketRequestExecuteIn,
	TicketRequestExecuteSpec,
	TicketRequestSpec,
	ProposalTicketIn,
)
from ...core.infrastructure import messaging as mq
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.
from ... import realtime

router = APIRouter(tags=["proposals"])


@router.post("/api/proposals", status_code=201)
def create_proposal(body: ProposalIn, s: Session = Depends(get_session),
                    authorization: str | None = Header(None)):
    """新建需求提案（初始 draft）。需为目标项目成员或管理员。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if not is_admin and not service.user_is_project_member(s, body.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        p = service.create_proposal(
            s, project_id=body.project_id, title=body.title, content=body.content,
            author_id=body.author_id if body.author_id is not None else uid,
            auto_create_ticket=body.auto_create_ticket,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(p)



@router.get("/api/proposals")
def list_proposals(
    project_id: int | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """列出提案，支持按 project_id / status 过滤与关键词搜索，默认 updated_at 倒序。"""
    uid = api_helpers._optional_user_id(authorization, s)
    try:
        rows = service.list_proposals(
            s, project_id=project_id, status=status, q=q,
            limit=limit, offset=offset, user_id=uid,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(p) for p in rows]



@router.get("/api/proposals/pending")
def list_pending_proposals(
    limit: int = Query(20, ge=1, le=200),
    s: Session = Depends(get_session),
):
    """Worker 拉取待认领提案（P1 先用 DB 轮询，P2 由 MQ 替换）。"""
    rows = service.list_proposals(s, status="queued", limit=limit)
    return [service._ser(p) for p in rows]


# 必须声明在 /api/proposals/{pid} 之前，否则 "reclaim-stale" 会被当作 pid 捕获。

@router.post("/api/proposals/reclaim-stale")
def reclaim_stale_proposals(
    body: ProposalReclaimIn | None = None, s: Session = Depends(get_session),
):
    """回收租约过期的 analyzing 提案（持有 Worker 已崩溃），批量回退 queued。

    判定依据是 claimed_at 而非 updated_at —— 后者会被用户作答等无关写入刷新，
    会让崩溃 Worker 的租约被无限续期。返回被回收的 proposal id 列表。
    """
    lease = (body.lease_seconds if body and body.lease_seconds is not None
             else service.DEFAULT_CLAIM_LEASE_SECONDS)
    try:
        ids = service.reclaim_stale_proposals(s, lease_seconds=lease)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 回收即重投：崩溃 Worker 留下的工作项重新进入队列，闭合「超时回退重投」回路。
    for pid in ids:
        api_helpers._dispatch_proposal(pid, 0, mq.REASON_RECLAIMED)
    return {"reclaimed": ids, "count": len(ids), "lease_seconds": lease}


# 必须声明在 /api/proposals/{pid} 之前，避免 "recover-failed" 被当作 pid 捕获。

@router.post("/api/proposals/recover-failed")
def recover_failed_proposals(
    body: RecoverFailedIn | None = None, s: Session = Depends(get_session),
):
    """后端 job：把「Agent 不可用」导致的 failed 提案自动回退 queued 重投。

    前端不做手动 retry —— agent 恢复后由本端点自动重试（受 window_seconds
    频率控制与 max_retries 上限约束，超限转人工）。与 reclaim-stale
    （analyzing 租约超时）互补，共同构成自动闭环的自愈回路。
    """
    window = (body.window_seconds if body and body.window_seconds is not None
              else 120)
    max_r = (body.max_retries if body and body.max_retries is not None else 5)
    try:
        ids = service.recover_failed_proposals(
            s, window_seconds=window, max_retries=max_r,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    for pid in ids:
        api_helpers._dispatch_proposal(pid, 0, mq.REASON_QUEUED)
    return {"recovered": ids, "count": len(ids),
            "window_seconds": window, "max_retries": max_r}



@router.get("/api/proposals/{pid}")
def get_proposal(pid: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_proposal(s, pid), "proposal"))



@router.patch("/api/proposals/{pid}")
def update_proposal(pid: int, body: ProposalPatch, s: Session = Depends(get_session)):
    """编辑提案正文 / 收敛规格 / 回填 story_id（状态流转请用 PUT /status）。"""
    try:
        r = service.update_proposal(s, pid, **body.model_dump(exclude_none=True))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(api_helpers._need(r, "proposal"))



@router.put("/api/proposals/{pid}/status")
def set_proposal_status(pid: int, body: ProposalStatusIn, s: Session = Depends(get_session)):
    """澄清状态机流转：draft→queued→analyzing→awaiting→answered→converged→story_created。

    非法迁移返回 400。失败态可带 error 说明并回退 queued 重投。
    """
    try:
        p = service.set_proposal_status(s, pid, body.status, error=body.error)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 进入 queued 即投递派发消息：覆盖 draft→queued 提交、failed→queued 重投、
    # analyzing→queued 超时回退三条入队边，无需各调用方各自记得发消息。
    if p is not None and str(p.status) == "queued":
        api_helpers._dispatch_proposal(pid, getattr(p, "current_round", 0) or 0,
                           mq.REASON_QUEUED)
    # 收敛后自动创建一条 auto 转换请求。Agent 会读项目层级后
    # 在 epic / story / task 中选择，不由 UI 预先猜测。
    if p is not None and str(p.status) == "converged" and p.auto_create_ticket:
        try:
            req = service.create_ticket_request(s, pid, type="auto")
        except (service.NotFound, service.InvalidValue) as e:
            raise HTTPException(status_code=422, detail=str(e))
        mq.publish_workflow_event(
            mq.EVENT_TICKET_REQUESTED, "proposal", pid, ref_id=req.id,
        )
        p = service.get_proposal(s, pid)
    return service._ser(p)



@router.post("/api/proposals/{pid}/claim")
def claim_proposal(pid: int, body: ProposalClaimIn | None = None,
                   s: Session = Depends(get_session)):
    """**原子**认领提案：queued/answered → analyzing，供 Worker 竞争消费。

    与 `PUT /status` 的关键区别：状态机对同状态迁移是幂等 no-op（返回 200），
    因此 PUT 无法仲裁并发认领 —— N 个 Worker 会全部「认领成功」。本端点把判定与
    写入压进单条条件 UPDATE，由数据库仲裁，恰好一个胜出。

    - 200：认领成功，返回提案（含 claimed_by / claimed_at 租约字段）
    - 409：已被他人持有或当前状态不可认领（与 400 非法迁移语义区分）
    - 404：提案不存在
    """
    agent = body.agent if body else ""
    try:
        p = service.claim_proposal(s, pid, agent=agent)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    if p is None:
        current = service.get_proposal(s, pid)
        raise HTTPException(
            status_code=409,
            detail=(f"proposal {pid} 无法认领：当前状态为 "
                    f"{current.status if current else 'unknown'}，"
                    f"仅 queued/answered 可认领"),
        )
    return service._ser(p)



@router.post("/api/proposals/{pid}/convert")
def convert_proposal(pid: int, body: ProposalConvertIn,
                    authorization: str | None = Header(None),
                    s: Session = Depends(get_session)):
    """人工终审确认：把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    保留人类最后一道闸 —— 不直接由 WorkBuddy/Worker 调 create_story，必须经
    本端点由人工/管理员确认后才转化。基于 converged_spec 生成 Story（description
    存原文）与子 Task（``- [ ]`` 清单项），回填 proposal.story_id 并推进
    converged → story_created。幂等：重复调用返回既有 Story，不重复创建。

    - 200：转化成功，返回 {proposal, story, tasks}
    - 400：提案非 converged / converged_spec 为空 / Epic 不属于提案项目
    - 401：未登录（auth required 时）
    - 403：非项目 owner/admin
    - 404：提案或 Epic 不存在

    Review 2026-08-26 P1 #4 修复：原端点缺 authorization + project 权限校验。
    任何已登录用户都能调 conversion → 越权创建 Story/Tasks。
    修法：要求 caller 是项目 owner 或 admin。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    # 必须先拿到 proposal 才能检查项目权限
    p = service.get_proposal(s, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    api_helpers._enforce_owner_or_admin(s, p.project_id, uid, is_admin)

    try:
        story, tasks, p = service.convert_proposal_to_story(
            s, pid, epic_id=body.epic_id, title=body.title,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "proposal": service._ser(p),
        "story": service._ser(story),
        "tasks": [service._ser(t) for t in tasks],
    }



@router.get("/api/proposals/{pid}/task-graph")
def get_proposal_task_graph(pid: int,
                            authorization: str | None = Header(None),
                            s: Session = Depends(get_session)):
    """获取 Proposal 真实持久化的 TaskGraph（DB DAG）。

    Review 2026-08-26 P1 #3 修复：原端点返回的是基于 converged_spec 推演的
    "planned" 推演图，跟实际 DB 持久化的 Task / TaskDependency 不一致。
    修法：本端点必须查 DB（proposal.story_id 关联的 Story + Task + TaskDependency）；
    推演版另开 ``/task-graph/planned``。

    权限：项目成员读权限（与 proposal 其他 read 端点一致）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    p = service.get_proposal(s, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    api_helpers._enforce_member_or_admin(s, p.project_id, uid, _is_admin)
    try:
        # 真实 DB DAG（Fix 3 修复）
        return service.get_persisted_task_graph(s, pid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/proposals/{pid}/task-graph/planned")
def get_proposal_planned_task_graph(pid: int,
                                     authorization: str | None = Header(None),
                                     s: Session = Depends(get_session)):
    """获取 Proposal 推演的 TaskGraph（基于 converged_spec 解析）。

    Review 2026-08-26 P1 #3 修复：从 ``/task-graph`` 拆出来。
    推演图（planned）≠ 持久化图（persisted）—— 前者描述"如果按 spec 转换会得到什么"，
    后者描述"DB 实际有什么"。调用方按需选择：UI 转换前预览用 planned，转换后
    看真实 DAG 用 ``/task-graph``。

    权限：项目成员读权限。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    p = service.get_proposal(s, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    api_helpers._enforce_member_or_admin(s, p.project_id, uid, _is_admin)
    try:
        return service.build_proposal_task_graph(s, pid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/api/proposals/{pid}")
def delete_proposal(pid: int,
                    authorization: str | None = Header(None),
                    s: Session = Depends(get_session)):
    """删除提案。

    Review 2026-08-26 P1 #4 修复：原端点无任何权限校验，任何已登录用户都能删。
    修法：admin 或 proposal creator 才能删。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    p = service.get_proposal(s, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    # creator 优先，否则要求 admin
    if p.created_by_user_id != uid and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="delete requires admin or proposal creator",
        )
    if not service.delete_proposal(s, pid):
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"ok": True}


# ---------- Proposal → Ticket 异步转化（2026-08-08 文档 #59）----------
# URL 命名空间统一（2026-08-10 Epic 123 Step 1）：
#   - 全局管理端点迁移到 /api/admin/ticket-requests/*（admin-only）；
#   - RPC 动作收敛到 /api/ticket-requests:execute 与 /api/ticket-requests/{rid}/{action}；
#   - 旧 URL 全部保留（301/307 或内部转发），1 release 后下架。


@router.post("/api/ticket-requests:execute")
def execute_ticket_request_rpc(body: TicketRequestExecuteSpec,
                               s: Session = Depends(get_session),
                               authorization: str | None = Header(None)):
    """[RPC] agent 经 MCP 调用（proposal_create_ticket）：按 (proposal, type) 定位/
    创建请求并执行转换，事务内创建实体 + 回填 + ticket_created。

    URL 命名统一（2026-08-10）：proposal 从 body 取，不再嵌入 URL 路径。
    - 200：生成成功，返回 {proposal, request, ticket}
    - 409：请求正在生成中（processing），调用方轮询
    - 422：层级不合法 / 状态不符
    """
    pid = body.proposal_id
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    p = service.get_proposal(s, pid)
    if not p:
        raise HTTPException(status_code=404, detail=f"proposal {pid} not found")
    if not is_admin and not service.user_is_project_member(s, p.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        result = service.execute_ticket_request(
            s, pid, type=body.type, epic_id=body.epic_id,
            story_id=body.story_id, title=body.title, request_id=body.request_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=409 if "正在生成中" in str(e) else 422,
                            detail=str(e))
    mq.publish_workflow_event(mq.EVENT_TICKET_CREATED, "proposal", pid,
                              ref_id=result["request"]["id"])
    return result



@router.post("/api/ticket-requests/{rid}/{action}")
def ticket_request_action(rid: int, action: str,
                          body: dict | None = None,
                          s: Session = Depends(get_session),
                          authorization: str | None = Header(None)):
    """[RPC] 统一动作端点：execute / fail / claim（2026-08-10 命名统一）。

    替代旧 ``/api/proposals/{pid}/ticket-requests/{rid}/{action}``——rid 全局
    唯一，无需再在 URL 里携带 proposal。
    """
    req = service.get_ticket_request(s, rid)
    if not req:
        raise HTTPException(status_code=404, detail=f"ticket request {rid} not found")
    # inner 实现留在 api.py 顶层（Phase 5 拆分前定义），函数内延迟导入避免循环
    from ...api import (  # noqa: E402
        claim_ticket_request_inner, execute_ticket_request_by_id_inner,
        fail_ticket_request_inner,
    )
    if action == "execute":
        return execute_ticket_request_by_id_inner(rid, s, authorization)
    if action == "fail":
        return fail_ticket_request_inner(rid, (body or {}).get("error", ""), s)
    if action == "claim":
        return claim_ticket_request_inner(rid, s)
    raise HTTPException(status_code=404, detail=f"unknown action '{action}'")



@router.get("/api/ticket-requests/pending")
def list_pending_ticket_requests_deprecated(
    limit: int = Query(20, ge=1, le=200),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """[deprecated] 旧全局 pending 端点 → 301 到 /api/admin/ticket-requests/pending。

    权限语义与旧版一致：REQUIRE_AUTH=1 下非 admin 仍 403（重定向不泄露数据）。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return RedirectResponse(url="/api/admin/ticket-requests/pending"
                                  f"?limit={limit}", status_code=301)



@router.post("/api/ticket-requests/reclaim-stale")
def reclaim_stale_ticket_requests_deprecated(
    body: TicketReclaimIn | None = None, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """[deprecated] 旧全局 reclaim-stale → 内部转发 admin 端点（POST 无法 301 保方法）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    lease = (body.lease_seconds if body and body.lease_seconds is not None
             else service.DEFAULT_CLAIM_LEASE_SECONDS)
    try:
        ids = service.reclaim_stale_ticket_requests(s, lease_seconds=lease)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"reclaimed": ids, "count": len(ids), "lease_seconds": lease}



@router.post("/api/proposals/{pid}/ticket-requests", status_code=201)
def create_ticket_request(pid: int, body: ProposalTicketIn,
                          s: Session = Depends(get_session),
                          authorization: str | None = Header(None)):
    """用户点击「生成 ticket」：创建转换请求（幂等），proposal → ticket_preparing，
    发 MQ proposal.ticket_requested（worker 消费后拉起 agent 生成）。返回 201 请求。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    p = service.get_proposal(s, pid)
    if not p:
        raise HTTPException(status_code=404, detail=f"proposal {pid} not found")
    if not is_admin and not service.user_is_project_member(s, p.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        req = service.create_ticket_request(
            s, pid, type=body.type, epic_id=body.epic_id,
            story_id=body.story_id, title=body.title,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    mq.publish_workflow_event(mq.EVENT_TICKET_REQUESTED, "proposal", pid,
                              ref_id=req.id)
    return service._ser(req)



@router.get("/api/proposals/{pid}/ticket-requests")
def list_ticket_requests(pid: int,
                         authorization: str | None = Header(None),
                         s: Session = Depends(get_session)):
    """列出提案的转换请求（前端轮询生成状态：pending/processing/done/failed）。

    Review 2026-08-26 P1 #4 修复：原端点无 authorization + 权限校验。
    修法：项目成员可读（与 proposal 其他 read 端点一致）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s=s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    p = service.get_proposal(s, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    api_helpers._enforce_member_or_admin(s, p.project_id, uid, _is_admin)
    try:
        return [service._ser(r) for r in service.list_ticket_requests(s, pid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/proposals/{pid}/ticket-requests/execute-by-type")
def execute_ticket_request_by_type_deprecated(pid: int, body: TicketRequestExecuteIn,
                                              s: Session = Depends(get_session),
                                              authorization: str | None = Header(None)):
    """[deprecated] 旧 execute-by-type → 内部转发 RPC 端点（2026-08-10 命名统一）。

    旧 URL 保留 + 标 deprecated，1 release 后下架；语义与
    ``POST /api/ticket-requests:execute`` 完全一致。
    """
    return execute_ticket_request_rpc(
        TicketRequestExecuteSpec(
            proposal_id=pid, type=body.type, epic_id=body.epic_id,
            story_id=body.story_id, title=body.title,
        ),
        s=s, authorization=authorization,
    )



@router.post("/api/proposals/{pid}/ticket-requests/{rid}/execute")
def execute_ticket_request_by_id_deprecated(pid: int, rid: int,
                                            s: Session = Depends(get_session),
                                            authorization: str | None = Header(None)):
    """[deprecated] 旧 execute-by-id → 内部转发统一动作端点。"""
    from ...api import execute_ticket_request_by_id_inner  # noqa: E402 延迟导入防循环
    req = service.get_ticket_request(s, rid)
    if not req or req.proposal_id != pid:
        raise HTTPException(
            status_code=404,
            detail=f"ticket request {rid} 不属于 proposal {pid}",
        )
    return execute_ticket_request_by_id_inner(rid, s, authorization)



@router.post("/api/proposals/{pid}/ticket-requests/{rid}/fail")
def fail_ticket_request_deprecated(pid: int, rid: int, body: TicketFailIn | None = None,
                                   s: Session = Depends(get_session)):
    """[deprecated] 旧 fail → 内部转发统一动作端点。"""
    from ...api import fail_ticket_request_inner  # noqa: E402 延迟导入防循环
    req = service.get_ticket_request(s, rid)
    if not req or req.proposal_id != pid:
        raise HTTPException(
            status_code=404,
            detail=f"ticket request {rid} 不属于 proposal {pid}",
        )
    return fail_ticket_request_inner(rid, (body.error if body else ""), s)



@router.post("/api/proposals/{pid}/ticket-requests/{rid}/claim")
def claim_ticket_request_deprecated(pid: int, rid: int,
                                    s: Session = Depends(get_session)):
    """[deprecated] 旧 claim → 内部转发统一动作端点。"""
    from ...api import claim_ticket_request_inner  # noqa: E402 延迟导入防循环
    req0 = service.get_ticket_request(s, rid)
    if not req0 or req0.proposal_id != pid:
        raise HTTPException(
            status_code=404,
            detail=f"ticket request {rid} 不属于 proposal {pid}",
        )
    return claim_ticket_request_inner(rid, s)



@router.post("/api/proposals/{pid}/questions", status_code=201)
def ask_proposal_questions(
    pid: int,
    body: ProposalAskIn,
    background: BackgroundTasks,
    s: Session = Depends(get_session),
):
    """Agent 回写一轮 open questions，并把提案推进到 awaiting（仅 analyzing 可提问）。

    同一 (proposal, round) 重复提交幂等复用既有轮次，兜底 at-least-once 重投。

    P2-10: the SignalR notification goes out via FastAPI BackgroundTasks
    so the request returns as soon as the proposal is persisted, instead
    of waiting up to 2 s for the .NET BFF to acknowledge the bridge call.
    """
    try:
        result = service.add_proposal_questions(
            s, proposal_id=pid, questions=body.questions, round_no=body.round,
            summary=body.summary, agent=body.agent,
        )
        proposal = service.get_proposal(s, pid)
        if proposal is not None:
            realtime.schedule_proposal_questions(
                background,
                proposal_id=pid,
                project_id=proposal.project_id,
                round_no=result["round"]["round_no"],
            )
        return result
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))



@router.get("/api/proposals/{pid}/rounds")
def list_proposal_rounds(pid: int, s: Session = Depends(get_session)):
    """按轮次正序返回澄清历史（含每轮问题与作答），供前端问答工作台渲染。"""
    try:
        return service.list_proposal_rounds(s, pid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.put("/api/proposals/{qid}/answer")
def answer_proposal_question(qid: int, body: ProposalAnswerIn,
                             s: Session = Depends(get_session),
                             authorization: str | None = Header(None)):
    """用户逐条作答；unsure=true 表示标记不确定。整轮处理完自动推进 awaiting→answered。"""
    uid = api_helpers._optional_user_id(authorization, s)
    try:
        q = service.answer_proposal_question(
            s, qid, answer=body.answer, unsure=body.unsure, user_id=uid,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 整轮答完会自动 awaiting→answered，此时立刻推一条消息触发下一轮澄清，
    # 免去「用户答完还要干等一个轮询周期」的延迟。
    p = service.get_proposal(s, q.proposal_id)
    if p is not None and str(p.status) == "answered":
        api_helpers._dispatch_proposal(p.id, getattr(p, "current_round", 0) or 0,
                           mq.REASON_ANSWERED)
    return service._ser(q)


# Phase 5 拆分时曾把旧路径 /api/proposal-questions/{qid}/answer 误改为
# /api/proposals/{qid}/answer，但前端（api.service.ts answerProposalQuestion）
# 与 worker/测试仍用旧路径 → 2026-08-15 回归修复：加回旧路径兼容转发（零契约破坏）。
@router.put("/api/proposal-questions/{qid}/answer")
def answer_proposal_question_deprecated(qid: int, body: ProposalAnswerIn,
                                        s: Session = Depends(get_session),
                                        authorization: str | None = Header(None)):
    return answer_proposal_question(qid, body, s, authorization)


# ---------- Epic 22 Story 22.1: 审计日志中间件 ----------
