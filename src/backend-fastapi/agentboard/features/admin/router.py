"""Admin / meta endpoints (health, cache, audit-logs, overview, etc.).

Phase 5:从 api.py 拆出。本文件无 prefix,所有路径保留 /api/X 完整形式。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Header
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ...core.application import service
from ..identity.schemas import UserAdminPatch
from ..proposals.schemas import TicketReclaimIn
from ..scheduling.schemas import SprintPatch
from ..work_items.schemas import AdminClearAssignmentIn
from datetime import datetime
from ...models import ALL_TYPES, ALL_STATUSES, ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.

router = APIRouter(tags=["admin"])


@router.get("/api/meta")
def meta():
    return {"types": ALL_TYPES, "statuses": ALL_STATUSES, "priorities": ALL_PRIORITIES,
            "sprint_statuses": ALL_SPRINT_STATUSES,
            "schedule_types": ALL_SCHEDULE_TYPES, "run_statuses": ALL_RUN_STATUSES}


# ---------- Health ----------

@router.get("/api/health")
def health(s: Session = Depends(get_session)):
    """健康检查端点：探测 DB 连接、API 版本。不需要鉴权。"""
    db_status = "ok"
    try:
        s.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    return {
        "status": "ok",
        "database": db_status,
        "version": "0.4",
        "timestamp": datetime.now().isoformat(),
    }


# ---------- Auth ----------

@router.get("/api/sprints/{sid}")
def get_sprint(sid: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_sprint(s, sid), "sprint"))



@router.patch("/api/sprints/{sid}")
def update_sprint(sid: int, body: SprintPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_sprint(s, sid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(api_helpers._need(r, "sprint"))



@router.post("/api/sprints/{sid}/activate", status_code=200)
def activate_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        result = service.activate_sprint(s, sid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(result.project_id)
    return service._ser(result)



@router.post("/api/sprints/{sid}/complete", status_code=200)
def complete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        result = service.complete_sprint(s, sid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    api_helpers._invalidate_stats_cache(result.project_id)
    return service._ser(result)



@router.delete("/api/sprints/{sid}")
def delete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        if not service.delete_sprint(s, sid):
            raise HTTPException(status_code=404, detail="sprint not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}



@router.get("/api/sprints/{sid}/tasks")
def list_sprint_tasks(sid: int, s: Session = Depends(get_session),
                      limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    api_helpers._need(service.get_sprint(s, sid), "sprint")
    return [service._ser(t) for t in service.list_tasks(s, sprint_id=sid, limit=limit, offset=offset)]



@router.get("/api/sprints/{sid}/burndown")
def sprint_burndown(sid: int, s: Session = Depends(get_session)):
    """Sprint 燃尽图数据"""
    return service.get_sprint_burndown(s, sid)


# ---------- Attachment ----------

@router.get("/api/overview")
def dashboard_overview(
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """首页 Dashboard 单请求聚合统计（替代四级整树预加载）。

    可见性：admin → 全部项目；普通用户 → 成员项目；未登录（REQUIRE_AUTH=0
    本地开放模式）→ 空统计。权限由 require_business_auth + project_access_middleware
    整体把关：本端点非项目级路由，鉴权仅要求有效身份（若开启）。
    """
    uid = api_helpers._optional_user_id(authorization, s)
    return service.get_overview(s, uid)


# ---------- Cache Statistics (Epic 30 / Story 30.1 Task 802) ----------

@router.get("/api/cache/stats")
def cache_stats(s: Session = Depends(get_session)):
    """缓存命中率与容量统计。

    鉴权由 require_business_auth 中间件统一处理：
    - AGENTBOARD_REQUIRE_AUTH=1 时，需携带具备 api:read 权限的 Bearer/API Key；
    - 本地开放模式（默认）下公开可读。
    """
    return get_cache().stats()


# ---------- Admin: Users ----------

@router.get("/api/admin/users")
def admin_list_users(
    s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    api_helpers._require_admin(authorization, s)
    users, total = service.list_users(s, limit=limit, offset=offset)
    return {"items": [service._ser(x) for x in users], "total": total}



@router.patch("/api/admin/users/{uid}")
def admin_update_user(
    uid: int, body: UserAdminPatch, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    api_helpers._require_admin(authorization, s, permission="api:write")
    u = service.set_user_admin(s, uid, body.is_admin)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return service._ser(u)


# ---------- Admin: Projects ----------

@router.get("/api/admin/projects")
def admin_list_projects(
    s: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    api_helpers._require_admin(authorization, s)
    projects, total = service.list_all_projects_admin(s, limit=limit, offset=offset)
    return {"items": projects, "total": total}



@router.delete("/api/admin/projects/{pid}")
def admin_delete_project(
    pid: int, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    api_helpers._require_admin(authorization, s, permission="api:write")
    if not service.delete_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


# ---------- Epic 20: Data Export ----------

@router.get("/api/audit-logs")
def list_audit_logs(
    project_id: int | None = Query(None),
    entity_type: str | None = Query(None),
    entity_id: int | None = Query(None),
    user_id: int | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
):
    """获取审计日志列表。"""
    items, total = service.list_audit_logs(
        s, project_id=project_id, entity_type=entity_type,
        entity_id=entity_id, user_id=user_id, action=action,
        limit=limit, offset=offset,
    )
    return {"items": [service._ser(x) for x in items], "total": total}


# ---------- Epic 22 Story 22.2: 任务依赖关系 ----------

@router.delete("/api/dependencies/{did}")
def delete_dependency(did: int, s: Session = Depends(get_session)):
    """删除依赖关系。"""
    try:
        service.remove_task_dependency(s, did)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.post("/api/admin/tasks/{tid}/clear-assignment")
def admin_clear_task_assignment_endpoint(
    tid: int, body: AdminClearAssignmentIn | None = None,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """[admin] Force-clear a task's ``current_assignment_id`` FK.

    Escape hatch for tasks stuck in ``todo`` with a non-null
    ``current_assignment_id`` (owner died / durable workflow failed to
    release / operator wants to re-route). Marks the underlying
    ``TaskAssignment`` as ``superseded`` (audit-distinct from
    ``completed``) and clears the FK so all claim/apply/arbitrate
    paths can proceed. Does not change task status — caller decides
    what to do next.

    No-op when ``current_assignment_id`` is already NULL.

    See ``service.admin_clear_task_assignment`` docstring for the full
    rationale and audit semantics.
    """
    actor = api_helpers._require_admin(authorization, s, permission="api:write")
    reason = (body.reason if body else "") or ""
    try:
        updated = service.admin_clear_task_assignment(
            s, tid, admin_user_id=actor.id, reason=reason,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(updated)


# ---------- Epic 22 Story 22.3: 数据导入 ----------

@router.get("/api/admin/ticket-requests/pending")
def admin_list_pending_ticket_requests(
    limit: int = Query(20, ge=1, le=200),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """[admin] Worker 拉取待认领转换请求（status=pending），跨项目全局池。

    权限（2026-08-09 review 修复 + 2026-08-10 命名统一）：REQUIRE_AUTH=1 下仅
    admin 可访问（worker 服务账号须为 admin；避免任意登录用户枚举全部项目请求）。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return [service._ser(r) for r in service.list_pending_ticket_requests(s, limit=limit)]



@router.post("/api/admin/ticket-requests/reclaim-stale")
def admin_reclaim_stale_ticket_requests(
    body: TicketReclaimIn | None = None, s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """[admin] 回收处理中超时的转换请求（processing 停滞 → failed），proposal 回退 converged。

    权限（2026-08-09 review 修复 + 2026-08-10 命名统一）：REQUIRE_AUTH=1 下仅
    admin 可访问（worker 维护周期调用）。
    """
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


# ---------- Worker queue hygiene (Story 434 / 2026-09-09) ----------

from ..scheduling.worker_work import (  # noqa: E402  (single source of truth)
    GHOST_CLEANUP_CONFIRM, GHOST_DELETE_SQL, GHOST_SELECT_SQL, GHOST_WHERE_SQL)


class GhostCleanupIn(BaseModel):
    """Admin operator input to clean WorkerWork ghost rows.

    A ghost row is any worker_work record whose ``entity_type``/``entity_id``
    pair cannot resolve to a real Proposal/Task (empty/NULL type, or a
    non-positive id). Those rows were minted before ``Offer`` enforced its
    reference. They are unclaimable: ``claim`` rebuilt an ``Offer`` from the
    stored columns and Pydantic rejected it, so the worker saw an opaque 500
    (and, before the 409 ``ghost_work_row`` refusal landed, a misleading log).
    The relay also used to republish them, which is how a handful of rows stalled
    the whole queue.
    """
    dry_run: bool = True
    limit: int = Field(default=1000, ge=1, le=10000)
    confirm: str | None = Field(default=None, max_length=64)


@router.post("/api/admin/worker-work/cleanup-ghost")
def admin_cleanup_ghost_worker_work(
    body: GhostCleanupIn = GhostCleanupIn(),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """[admin] 删除/统计 worker_work 表中的 ghost 行。

    Ghost 判定复用 worker_work 模块里的唯一一份谓词(见 GHOST_WHERE_SQL),与
    relay/claim 拦截和 scripts/cleanup_ghost_work.py 保持同步。

    安全:默认 dry_run 只统计;真要删除必须带 confirm="delete-ghost-rows"。
    权限:REQUIRE_AUTH=1 下仅 admin 可访问(本地 open-CRUD 模式仍放行,故加 confirm)。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    if not body.dry_run and body.confirm != GHOST_CLEANUP_CONFIRM:
        raise HTTPException(
            status_code=422,
            detail=f'deleting ghost rows requires confirm="{GHOST_CLEANUP_CONFIRM}"')

    rows = s.execute(text(GHOST_SELECT_SQL), {"limit": body.limit}).mappings().all()
    ids = [int(r["id"]) for r in rows]
    samples = [
        {"id": int(r["id"]), "project_id": int(r["project_id"]),
         "entity_type": r["entity_type"], "entity_id": r["entity_id"],
         "kind": r["kind"], "state": r["state"]}
        for r in rows[:20]
    ]
    # Deleting the queue row leaves worker_discussions.source_work_id dangling
    # (it is a plain Integer, not an FK), so report the blast radius first.
    linked_discussions = 0
    if ids:
        linked = text(
            "SELECT COUNT(*) AS n FROM worker_discussions "
            "WHERE source_work_id IN :ids").bindparams(bindparam("ids", expanding=True))
        linked_discussions = int(s.execute(linked, {"ids": ids}).scalar() or 0)
    deleted = 0
    if ids and not body.dry_run:
        del_stmt = text(GHOST_DELETE_SQL).bindparams(bindparam("ids", expanding=True))
        result = s.execute(del_stmt, {"ids": ids})
        s.commit()
        deleted = int(result.rowcount or 0)
    return {
        "matched": len(ids),
        "deleted": deleted,
        "dry_run": body.dry_run,
        "limit": body.limit,
        "linked_discussions": linked_discussions,
        "sample": samples,
        "ids": ids if not body.dry_run else ids[:20],
    }


@router.get("/api/admin/worker-work/inspect-ghost")
def admin_inspect_ghost_worker_work(
    limit: int = Query(50, ge=1, le=500),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """[admin] 调试用:列出 state=available 的 worker_work 行,以及 raw entity_type/entity_id。

    用于排查"看起来是 ghost 但 cleanup-ghost 端点 matched=0"的场景(可能是 SQL
    谓词/编码差异)。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and not is_admin:
        raise HTTPException(status_code=403, detail="admin required")

    count_stmt = text("SELECT COUNT(*) AS n FROM worker_work WHERE state = 'available'")
    total = int(s.execute(count_stmt).scalar() or 0)

    stmt = text(
        "SELECT id, project_id, entity_type, entity_id, kind, state, "
        "       LENGTH(entity_type) AS et_len, "
        "       HEX(entity_type) AS et_hex, "
        f"       CASE WHEN {'(' + GHOST_WHERE_SQL + ')'} THEN 1 ELSE 0 END AS is_ghost "
        "FROM worker_work WHERE state = 'available' "
        "ORDER BY id ASC LIMIT :limit"
    )
    rows = s.execute(stmt, {"limit": limit}).mappings().all()
    samples = [
        {
            "id": int(r["id"]),
            "project_id": int(r["project_id"]),
            "entity_type": r["entity_type"],
            "entity_id": r["entity_id"],
            "entity_type_len": r["et_len"],
            "entity_type_hex": r["et_hex"],
            "kind": r["kind"],
            "state": r["state"],
            "is_ghost": bool(r["is_ghost"]),
        }
        for r in rows
    ]
    return {"total_available": total, "limit": limit, "rows": samples}
