"""Scheduling feature router (Phase 5 split from api.py)。

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
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.
from ...api import agent_state_hub  # noqa: E402 — Agent 状态 WebSocket 广播 hub（定义于 api.py 顶层）

router = APIRouter(tags=["scheduling"])


@router.post("/api/agents/register", status_code=201)
def register_agent(body: AgentRegisterIn, authorization: str | None = Header(None),
                   s: Session = Depends(get_session)):
    """注册/更新 Agent 身份（幂等，MCP/agent 自报入口）。绑定当前认证用户。

    2026-08-20 Epic 151 / Task 1297a：返回字段按 caller 角色分（admin→to_admin_dict，
    普通用户/Agent 自报→to_public_dict）。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.register_agent(s, agent_id=body.agent_id, name=body.name,
                                   roles=body.roles, capabilities=body.capabilities,
                                   cli_command=body.cli_command, model=body.model,
                                   auth_key=body.auth_key, user_id=uid)
    payload = agent.to_admin_dict() if is_admin else agent.to_public_dict()
    agent_state_hub.broadcast_agent(agent.to_public_dict())
    return payload



@router.put("/api/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdateIn,
                 authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """前端 Agent 配置中心：更新名称/角色/CLI 模板/模型/启用状态（全字段可选）。

    2026-08-20 Epic 151 / Task 1297a：admin/owner 返回 to_admin_dict；其他用户
    to_public_dict。WS 广播统一 to_public_dict。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.get_agent_by_agent_id(s, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    if not is_admin and agent.user_id not in (None, uid):
        raise HTTPException(status_code=403, detail="agent belongs to another user")
    agent = service.update_agent(s, agent_id, **body.model_dump(exclude_none=True))
    is_owner = (agent.user_id == uid)
    payload = agent.to_admin_dict() if (is_admin or is_owner) else agent.to_public_dict()
    agent_state_hub.broadcast_agent(agent.to_public_dict())
    return payload



@router.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str, authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    """删除 Agent 注册记录（前端配置中心）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.get_agent_by_agent_id(s, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    if not is_admin and agent.user_id not in (None, uid):
        raise HTTPException(status_code=403, detail="agent belongs to another user")
    service.delete_agent(s, agent_id)
    agent_state_hub.broadcast_deleted(agent_id)
    return {"ok": True}



@router.post("/api/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str, body: AgentHeartbeatIn | None = None,
                    authorization: str | None = Header(None),
                    s: Session = Depends(get_session)):
    """Agent 心跳保活（置在线）。Worker probe 带 probe_ok/probe_message 上报详情。

    2026-08-20 Epic 151 / Task 1297a：caller 是 Agent 自己，永远返回 to_public_dict
    （Agent 不需要看自己的 auth_key / cli_command）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    probe_ok = body.probe_ok if body else None
    probe_message = body.probe_message if body else ""
    agent = service.agent_heartbeat(s, agent_id, user_id=uid,
                                    probe_ok=probe_ok, probe_message=probe_message)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    payload = agent.to_public_dict()
    agent_state_hub.broadcast_agent(payload)
    return payload



@router.post("/api/agents/{agent_id}/deregister")
def agent_deregister(agent_id: str, body: AgentHeartbeatIn | None = None,
                     authorization: str | None = Header(None),
                     s: Session = Depends(get_session)):
    """Agent 注销下线（自身或 admin）。Worker probe 失败带 probe_message 原因。

    2026-08-20 Epic 151 / Task 1297a：admin 调可拿 to_admin_dict（看 probe_message 详情），
    Agent 自调用 to_public_dict。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    probe_message = body.probe_message if body else ""
    agent = service.agent_deregister(s, agent_id, user_id=uid, is_admin=is_admin,
                                     probe_message=probe_message)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    payload = agent.to_admin_dict() if is_admin else agent.to_public_dict()
    agent_state_hub.broadcast_agent(agent.to_public_dict())
    return payload



@router.post("/api/agents/{agent_id}/probe")
def probe_agent(agent_id: str, body: AgentProbeIn | None = None,
                authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """手动探测 Agent CLI（前端「立即探测」）：dry-run 解析命令（B-A2 整改）。

    历史 RCE：原同步跑 ``<cmd> --version`` 判活，dev 默认 ``REQUIRE_AUTH=0``
    匿名可调 → 任意命令执行。现改为 dry-run：仅校验 + 返回"将要执行的命令"，
    实际判活交给 Worker 心跳（``heartbeat.probe_cli``，受信本地进程）。

    **强制鉴权**（B-A2）：dev 模式（``REQUIRE_AUTH=0``）也要求登录，不再
    匿名放行——与 ``_auth_is_required()`` 软判定解耦。

    2026-08-20 Epic 151 / Task 1297a：probe 端点 caller 必登录，admin 可拿 to_admin_dict
    （看 cli_command / probe_message 详情），普通用户 to_public_dict。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if uid is None:  # B-A2: probe 端点永远要求鉴权（不再 _auth_is_required 软判定）
        raise HTTPException(status_code=401, detail="unauthorized")
    agent = service.get_agent_by_agent_id(s, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    timeout = body.timeout if body else 8
    ok, msg = api_helpers._probe_cli_sync(agent.cli_command, model=agent.model, timeout=timeout)
    agent = service.agent_heartbeat(s, agent_id, user_id=uid,
                                    probe_ok=ok, probe_message=msg)
    payload = agent.to_admin_dict() if is_admin else agent.to_public_dict()
    agent_state_hub.broadcast_agent(agent.to_public_dict())
    return payload



@router.get("/api/agents")
def list_agents(online: bool | None = Query(None), role: str | None = Query(None),
                authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """列出已注册 Agent（?online=true&role=reviewer 过滤）。

    2026-08-20 Epic 151 Story 326 Task 1297：档 A 阻断级 — MembersTab 数据边界。
    - 软鉴权：``AGENTBOARD_REQUIRE_AUTH=1`` 时未登录返回 401；dev 模式放行。
    - 字段收窄：用 ``Agent.to_public_dict`` 替代 ``service._ser``，脱敏
      ``cli_command`` / ``auth_key`` / ``probe_message`` / ``user_id``。
    - 排序：按 ``created_at`` 倒序（注册时间新→旧），前端默认展示一致。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    rows = service.list_agents(s, online=online, role=role, order_by_created=True)
    return [a.to_public_dict() for a in rows]



@router.get("/api/runs")
def list_run_records_api(
    agent: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=20),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """Worker operations view: enriched, filterable AgentRun records."""
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    try:
        return service.list_run_records(
            s, agent=agent, status=status, q=q, limit=limit, offset=offset,
            user_id=uid,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------- Sprint ----------

@router.get("/api/schedules/{sid}")
def get_schedule(sid: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_schedule(s, sid), "schedule"))



@router.patch("/api/schedules/{sid}")
def update_schedule(sid: int, body: SchedulePatch, s: Session = Depends(get_session)):
    fields = body.model_dump(exclude_none=True)
    for k in ("agent", "task_id", "task_priority", "task_type", "epic_id"):
        if k in body.model_fields_set:
            fields[k] = getattr(body, k)  # 显式 null = 解除绑定 / 清除筛选
    try:
        r = service.update_schedule(s, sid, **fields)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return service._ser(api_helpers._need(r, "schedule"))



@router.delete("/api/schedules/{sid}")
def delete_schedule(sid: int, s: Session = Depends(get_session)):
    if not service.delete_schedule(s, sid):
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True}


# ---------- AgentRun ----------

@router.post("/api/schedules/{sid}/runs", status_code=201)
def create_run(sid: int, body: RunIn, s: Session = Depends(get_session)):
    try:
        run = service.create_run(s, schedule_id=sid, task_id=body.task_id,
                                 idempotency_key=body.idempotency_key)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(run)



@router.get("/api/schedules/{sid}/runs")
def list_runs(sid: int, s: Session = Depends(get_session),
              limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    api_helpers._need(service.get_schedule(s, sid), "schedule")
    return [service._ser(r) for r in service.list_runs(s, sid, limit=limit, offset=offset)]



@router.get("/api/runs/{rid}")
def get_run(rid: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_run(s, rid), "run"))



@router.patch("/api/runs/{rid}")
def update_run(rid: int, body: RunPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_run(s, rid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(api_helpers._need(r, "run"))



@router.post("/api/runs/{rid}/report")
def report_run_result(rid: int, body: RunReportIn, s: Session = Depends(get_session)):
    """Agent 主动报告 run 结果（Epic 78 Story 104）：
    仅 pending/running → success/failed/cancelled 合法；终态不可再变（幂等除外）。
    """
    try:
        r = service.report_run_result(
            s, rid, status=body.status, summary=body.summary, log_ref=body.log_ref,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(r)



@router.delete("/api/runs/{rid}")
def delete_run(rid: int, s: Session = Depends(get_session)):
    if not service.delete_run(s, rid):
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}


# ---------- Project visibility & members ----------


# ---------- Project Members ----------
