"""Admin / meta endpoints (health, cache, audit-logs, overview, etc.).

Phase 5:从 api.py 拆出。本文件无 prefix,所有路径保留 /api/X 完整形式。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Header
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ... import service
from agentboard.schemas import *  # Phase 5: forward-ref-safe
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
