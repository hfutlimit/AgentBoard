"""Scheduling feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session, SessionLocal
from ...core.application import service
from .schemas import (
	AgentHeartbeatIn,
	AgentInstanceHeartbeatIn,
	AgentInstanceUpsertIn,
	AgentProbeIn,
	AgentRegisterIn,
	AgentUpdateIn,
	RunIn,
	RunPatch,
	RunReportIn,
	SchedulePatch,
	WorkerRegisterIn,
)
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.
from ...api import agent_state_hub  # noqa: E402 — Agent 状态 WebSocket 广播 hub（定义于 api.py 顶层）
from .models import AgentRun, RunEvent  # noqa: E402 — P1-4 SSE watermark snapshot
from .run_event_bus import (
    IRunEventBus,
    InProcessRunEventBus,
    RESYNC_REQUIRED_CONTROL,
)  # noqa: E402 — P1-7 broker-ready bus


router = APIRouter(tags=["scheduling"])

from .behavior_router import router as behavior_router  # noqa: E402
router.include_router(behavior_router)

log = logging.getLogger("agentboard.features.scheduling.router")


# P1-7: the bus is now an in-process implementation behind the
# ``IRunEventBus`` protocol. Tests and the local dev path keep using the
# in-memory bus; a future ``MqRunEventBus`` (RabbitMQ topic
# ``agentboard.run.events``) can replace it without touching the router.
run_event_bus: IRunEventBus = InProcessRunEventBus()

class RunEventIn(BaseModel):
    event_type: str
    payload: dict

def _decode_event_payload(payload: str | dict) -> str | dict:
    if not isinstance(payload, str):
        return payload
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload


def _event_to_wire(event) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "event_type": event.event_type,
        "payload": _decode_event_payload(event.payload),
        "actor_user_id": getattr(event, "actor_user_id", None),
        "api_key_id": getattr(event, "api_key_id", None),
        "agent_registry_id": getattr(event, "agent_registry_id", None),
        "worker_id": getattr(event, "worker_id", None),
        "actor_username_snapshot": getattr(event, "actor_username_snapshot", None),
        "api_key_prefix_snapshot": getattr(event, "api_key_prefix_snapshot", None),
        "agent_ref_snapshot": getattr(event, "agent_ref_snapshot", None),
        "created_at": event.created_at.isoformat(),
    }


def _format_sse(event: dict) -> str:
    event_type = str(event.get("event_type") or "message").replace("\r", " ").replace("\n", " ")
    return (
        f"id: {event['id']}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


_TERMINAL_RUN_STATUSES = {"success", "failed", "cancelled"}


def _is_terminal_event(event: dict) -> bool:
    event_payload = event.get("payload")
    event_status = (
        event_payload.get("status")
        if isinstance(event_payload, dict)
        else None
    )
    return event_status in _TERMINAL_RUN_STATUSES or event.get("event_type") in {
        "run.success",
        "run.failed",
        "run.cancelled",
    }


@router.post("/api/agent-runs/{run_id}/events", status_code=201)
def create_run_event_endpoint(
    run_id: int,
    body: RunEventIn,
    authorization: str | None = Header(None),
    worker_id: str | None = Header(None, alias="X-Worker-ID"),
    s: Session = Depends(get_session),
):
    run, actor = api_helpers._authorize_run_mutation(
        authorization, s, run_id, operation="event", worker_id=worker_id,
    )
    try:
        run_event = service.create_run_event(
            s,
            run_id=run_id,
            event_type=body.event_type,
            payload=body.payload,
            actor_user_id=actor.user_id if actor else None,
            api_key_id=actor.api_key_id if actor else None,
            agent_registry_id=actor.agent_registry_id if actor else None,
            worker_id=api_helpers._run_lease_worker_id(run, worker_id),
            actor_username_snapshot=actor.username if actor else None,
            api_key_prefix_snapshot=actor.api_key_prefix if actor else None,
            agent_ref_snapshot=actor.agent_ref if actor else None,
        )
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = _event_to_wire(run_event)
    run_event_bus.broadcast(run_id, payload)
    return payload


@router.get("/api/agent-runs/{run_id}/events")
def list_run_events_endpoint(
    run_id: int,
    authorization: str | None = Header(None),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(200, ge=1, le=200),
    s: Session = Depends(get_session),
):
    api_helpers._authorize_run_read(authorization, s, run_id)
    # Service returns events in the requested order so the router can be
    # explicit. Without ``before_id`` we want ascending (oldest first) so
    # the client can stream-replay; with ``before_id`` we want the newest
    # events strictly less than the cursor so pagination is O(limit).
    order = "desc" if before_id is not None else "asc"
    rows = service.list_run_events(
        s, run_id=run_id, before_id=before_id, limit=limit, order=order,
    )
    return [_event_to_wire(event) for event in rows]


@router.get("/api/agent-runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: int,
    request: Request,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    # P0-1: read auth is enforced here too — the SSE response includes the
    # same audit metadata as the JSON list endpoint, so a member of another
    # project must not be able to subscribe to a foreign run's event stream.
    api_helpers._authorize_run_read(authorization, s, run_id)
    run = service.get_run(s, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    initial_run_status = str(run.status)
    initial_finished_at = run.finished_at.isoformat() if run.finished_at else ""
    try:
        last_event_id = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        last_event_id = 0
    subscription = run_event_bus.subscribe(run_id)
    try:
        # Subscribe before taking the snapshot so events published during the
        # replay query are queued. Keep only the watermark here; the response
        # generator reads and yields pages lazily so a large backlog cannot be
        # duplicated in memory per SSE connection.
        #
        # P1-4 (SSE replay gap): paginate to the snapshot MAX(id) so no
        # events between the client's cursor and the watermark are silently
        # dropped. The page data is intentionally not materialized here.
        snapshot_max_id = (
            s.query(RunEvent.id)
            .filter(RunEvent.run_id == run_id, RunEvent.id > last_event_id)
            .order_by(RunEvent.id.desc())
            .first()
        )
        snapshot_max_id = snapshot_max_id[0] if snapshot_max_id else last_event_id
    except Exception:
        run_event_bus.unsubscribe(run_id, subscription)
        raise
    finally:
        s.close()

    async def event_generator():
        try:
            replay_high_watermark = last_event_id
            saw_terminal_event = False
            with SessionLocal() as replay_session:
                while replay_high_watermark < snapshot_max_id:
                    page = service.list_run_events(
                        replay_session,
                        run_id=run_id,
                        after_id=replay_high_watermark,
                        limit=200,
                    )
                    if not page:
                        break
                    page_start_watermark = replay_high_watermark
                    for event in page:
                        payload = _event_to_wire(event)
                        replay_high_watermark = max(
                            replay_high_watermark, payload["id"],
                        )
                        saw_terminal_event = (
                            saw_terminal_event or _is_terminal_event(payload)
                        )
                        yield _format_sse(payload)
                    if replay_high_watermark <= page_start_watermark:
                        break

            if initial_run_status in _TERMINAL_RUN_STATUSES:
                if not saw_terminal_event:
                    yield _format_sse({
                        "id": replay_high_watermark,
                        "run_id": run_id,
                        "event_type": f"run.{initial_run_status}",
                        "payload": {"status": initial_run_status},
                        "created_at": initial_finished_at,
                    })
                return
            ping_count = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(subscription.queue.get(), timeout=1)
                    if data.get("_control") == RESYNC_REQUIRED_CONTROL:
                        log.warning(
                            "SSE subscriber lagged; closing run %s stream for replay resync",
                            run_id,
                        )
                        return
                    if data["id"] <= replay_high_watermark:
                        continue
                    replay_high_watermark = data["id"]
                    yield _format_sse(data)
                    if _is_terminal_event(data):
                        return
                except asyncio.TimeoutError:
                    ping_count += 1
                    if ping_count >= 15:
                        yield ": ping\n\n"
                        ping_count = 0
                    with SessionLocal() as live_session:
                        live_run = service.get_run(live_session, run_id)
                        if live_run is None or str(live_run.status) in _TERMINAL_RUN_STATUSES:
                            if live_run is not None:
                                yield _format_sse({
                                    "id": replay_high_watermark,
                                    "run_id": run_id,
                                    "event_type": f"run.{live_run.status}",
                                    "payload": {"status": str(live_run.status)},
                                    "created_at": live_run.finished_at.isoformat() if live_run.finished_at else "",
                                })
                            return
        finally:
            run_event_bus.unsubscribe(run_id, subscription)
    return StreamingResponse(event_generator(), media_type="text/event-stream")



@router.post("/api/agents/register", status_code=201)
def register_agent(body: AgentRegisterIn, authorization: str | None = Header(None),
                   s: Session = Depends(get_session)):
    """注册/更新 Agent 身份（幂等，MCP/agent 自报入口）。绑定当前认证用户。

    2026-08-20 Epic 151 / Task 1297a：返回字段按 caller 角色分（admin→to_admin_dict，
    普通用户/Agent 自报→to_public_dict）。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
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
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
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
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
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
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
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
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
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
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
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
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    rows = service.list_agents(s, online=online, role=role, order_by_created=True)
    return [a.to_public_dict() for a in rows]


# ---------- Worker + AgentInstance（2026-08-26 P1：多 Worker 部署隔离） ----------

@router.post("/api/workers/register", status_code=201)
def register_worker_endpoint(body: WorkerRegisterIn,
                              authorization: str | None = Header(None),
                              s: Session = Depends(get_session)):
    """Worker 启动时自报身份（幂等）。

    软鉴权：dev 模式放行；生产（``AGENTBOARD_REQUIRE_AUTH=1``）要求登录。
    Worker 不绑 user —— 跨用户部署。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    w = service.register_worker(s, worker_id=body.worker_id,
                                hostname=body.hostname, status=body.status)
    return w.to_public_dict()


@router.get("/api/workers")
def list_workers_endpoint(authorization: str | None = Header(None),
                           s: Session = Depends(get_session)):
    """列出所有 Worker（admin 视图）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return [w.to_public_dict() for w in service.list_workers(s)]


@router.get("/api/workers/{worker_id}/instances")
def list_worker_instances(worker_id: str,
                           authorization: str | None = Header(None),
                           s: Session = Depends(get_session)):
    """Worker 拿本机 instances（owner 视角，含 ``cli_command``）。

    软鉴权。Worker 自己的 abk_ key 即可（``AGENTBOARD_WORKER_ID`` 与 ``worker_id``
    一致时由 Worker 端走专用路径，不需要 admin）。``cli_command`` 在 owner 视角下
    必须返回 —— Worker 探测 CLI 要用。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not service.get_worker_by_id(s, worker_id):
        raise HTTPException(status_code=404, detail=f"worker {worker_id} not found")
    rows = service.list_agent_instances(s, worker_id=worker_id)
    return [inst.to_owner_dict() for inst in rows]


@router.post("/api/agents/{agent_id}/instances", status_code=201)
def upsert_agent_instance_for_agent(agent_id: str, body: AgentInstanceUpsertIn,
                                      authorization: str | None = Header(None),
                                      s: Session = Depends(get_session)):
    """Worker 给某 logical agent 挂本机 instance（POST /api/agents/{id}/instances）。

    软鉴权。``body.worker_id`` 必须已注册（先 ``POST /api/workers/register``）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if body.worker_id != agent_id and True:  # 留 hook：未来可校验 worker 身份
        pass
    try:
        inst = service.upsert_agent_instance(
            s, worker_id=body.worker_id, agent_id=agent_id,
            cli_command=body.cli_command, model=body.model,
            auth_key=body.auth_key, enabled=body.enabled,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return inst.to_owner_dict()


@router.get("/api/agent-instances")
def list_agent_instances_endpoint(worker_id: str | None = Query(None),
                                   agent_id: str | None = Query(None),
                                   authorization: str | None = Header(None),
                                   s: Session = Depends(get_session)):
    """列 AgentInstance。

    - ``worker_id`` 非空：owner 视角，含 ``cli_command``（Worker 自查或 admin）；
    - ``agent_id`` 非空：跨 worker 视角，脱敏 ``cli_command``（聚合视图）。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    rows = service.list_agent_instances(s, worker_id=worker_id, agent_id=agent_id)
    if worker_id and not agent_id:
        # owner 视角：含 cli_command
        return [inst.to_owner_dict() for inst in rows]
    # 跨 worker 视角：脱敏
    return [inst.to_public_dict() for inst in rows]


@router.get("/api/agent-instances/{instance_id}")
def get_agent_instance_endpoint(instance_id: int,
                                 authorization: str | None = Header(None),
                                 s: Session = Depends(get_session)):
    """单条 AgentInstance（owner 视角，含 ``cli_command``）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    inst = service.get_agent_instance(s, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="instance not found")
    return inst.to_owner_dict()


@router.delete("/api/agent-instances/{instance_id}")
def delete_agent_instance_endpoint(instance_id: int,
                                    authorization: str | None = Header(None),
                                    s: Session = Depends(get_session)):
    """删除 AgentInstance（admin 删；Worker 主动 deregister 走
    ``POST /api/agent-instances/{id}/deregister``，不删记录）。"""
    uid, is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    ok = service.delete_agent_instance(s, instance_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"ok": True}


@router.post("/api/workers/{worker_id}/agent-instances/{instance_id}/heartbeat")
def instance_heartbeat_endpoint(worker_id: str, instance_id: int,
                                  body: AgentInstanceHeartbeatIn,
                                  authorization: str | None = Header(None),
                                  s: Session = Depends(get_session)):
    """Instance 心跳（Worker 探测成功后上报）。

    **强制 ownership 校验**（2026-08-26 P1 修复）：URL path 的 ``worker_id`` 必须与
    ``instance.worker_id`` 一致，否则 403。多 Worker 部署下这是防 A 覆盖 B 健康
    instance 的关键闸门（caller 不能不带 worker_id 调通）。
    """
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        inst = service.instance_heartbeat(
            s, instance_id, caller_worker_id=worker_id,
            probe_ok=body.probe_ok, probe_message=body.probe_message,
        )
    except Exception as e:
        from ...core.exceptions import Forbidden as _F, NotFound as _N, InvalidValue as _IV
        if isinstance(e, _F):
            raise HTTPException(status_code=403, detail=str(e))
        if isinstance(e, _N):
            raise HTTPException(status_code=404, detail=str(e))
        if isinstance(e, _IV):
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    return inst.to_owner_dict()


@router.post("/api/workers/{worker_id}/agent-instances/{instance_id}/deregister")
def instance_deregister_endpoint(worker_id: str, instance_id: int,
                                   body: AgentInstanceHeartbeatIn,
                                   authorization: str | None = Header(None),
                                   s: Session = Depends(get_session)):
    """Instance 注销（Worker 探测失败后上报原因）。同 heartbeat 路径，URL worker_id 强校验。"""
    uid, _is_admin = api_helpers._caller_uid_admin(authorization, s)
    if api_helpers._auth_is_required() and uid is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        inst = service.instance_deregister(
            s, instance_id, caller_worker_id=worker_id,
            probe_message=body.probe_message,
        )
    except Exception as e:
        from ...core.exceptions import Forbidden as _F, NotFound as _N, InvalidValue as _IV
        if isinstance(e, _F):
            raise HTTPException(status_code=403, detail=str(e))
        if isinstance(e, _N):
            raise HTTPException(status_code=404, detail=str(e))
        if isinstance(e, _IV):
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    return inst.to_owner_dict()



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


@router.get("/api/tasks/{task_id}/runs")
def list_task_runs_api(
    task_id: int,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    uid = api_helpers._current_user(authorization, s, required_permission="api:read").id
    api_helpers._need(service.get_task(s, task_id), "task")
    return service.list_run_records(
        task_id=task_id, limit=limit, offset=offset, user_id=uid, s=s,
    )


# ---------- Sprint ----------

@router.get("/api/schedules/{sid}")
def get_schedule(sid: int, authorization: str | None = Header(None),
                 s: Session = Depends(get_session)):
    api_helpers._authorize_schedule_read(authorization, s, sid)
    return service._ser(api_helpers._need(service.get_schedule(s, sid), "schedule"))



@router.patch("/api/schedules/{sid}")
def update_schedule(sid: int, body: SchedulePatch,
                    authorization: str | None = Header(None),
                    s: Session = Depends(get_session)):
    api_helpers._authorize_schedule_write(authorization, s, sid)
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
def delete_schedule(sid: int, authorization: str | None = Header(None),
                    s: Session = Depends(get_session)):
    api_helpers._authorize_schedule_write(authorization, s, sid)
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
def list_runs(sid: int, authorization: str | None = Header(None),
              s: Session = Depends(get_session),
              limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    api_helpers._authorize_schedule_read(authorization, s, sid)
    return [service._ser(r) for r in service.list_runs(s, sid, limit=limit, offset=offset)]



@router.get("/api/runs/{rid}")
def get_run(rid: int, authorization: str | None = Header(None),
            s: Session = Depends(get_session)):
    api_helpers._authorize_run_read(authorization, s, rid)
    return service._ser(api_helpers._need(service.get_run(s, rid), "run"))



@router.patch("/api/runs/{rid}")
def update_run(
    rid: int,
    body: RunPatch,
    authorization: str | None = Header(None),
    worker_id: str | None = Header(None, alias="X-Worker-ID"),
    s: Session = Depends(get_session),
):
    run, actor = api_helpers._authorize_run_mutation(
        authorization, s, rid, operation="patch", worker_id=worker_id,
    )
    try:
        before = service.get_run(s, rid)
        before_status = str(before.status) if before is not None else None
        r = service.update_run(s, rid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    r = api_helpers._need(r, "run")
    if before_status != str(r.status) and str(r.status) in {"success", "failed", "cancelled"}:
        event = service.create_run_event(
            s, run_id=rid, event_type=f"run.{r.status}", payload={"status": r.status},
            actor_user_id=actor.user_id if actor else None,
            api_key_id=actor.api_key_id if actor else None,
            agent_registry_id=actor.agent_registry_id if actor else None,
            worker_id=api_helpers._run_lease_worker_id(before, worker_id),
            actor_username_snapshot=actor.username if actor else None,
            api_key_prefix_snapshot=actor.api_key_prefix if actor else None,
            agent_ref_snapshot=actor.agent_ref if actor else None,
        )
        run_event_bus.broadcast(rid, _event_to_wire(event))
    return service._ser(r)



@router.post("/api/runs/{rid}/report")
def report_run_result(
    rid: int,
    body: RunReportIn,
    authorization: str | None = Header(None),
    worker_id: str | None = Header(None, alias="X-Worker-ID"),
    s: Session = Depends(get_session),
):
    """Agent 主动报告 run 结果（Epic 78 Story 104）：
    仅 pending/running → success/failed/cancelled 合法；终态不可再变（幂等除外）。
    """
    run, actor = api_helpers._authorize_run_mutation(
        authorization, s, rid, operation="report", worker_id=worker_id,
    )
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
    if str(r.status) in {"success", "failed", "cancelled"}:
        event = service.create_run_event(
            s, run_id=rid, event_type=f"run.{r.status}", payload={"status": r.status},
            actor_user_id=actor.user_id if actor else None,
            api_key_id=actor.api_key_id if actor else None,
            agent_registry_id=actor.agent_registry_id if actor else None,
            worker_id=api_helpers._run_lease_worker_id(run, worker_id),
            actor_username_snapshot=actor.username if actor else None,
            api_key_prefix_snapshot=actor.api_key_prefix if actor else None,
            agent_ref_snapshot=actor.agent_ref if actor else None,
        )
        run_event_bus.broadcast(rid, _event_to_wire(event))
    return service._ser(r)



@router.delete("/api/runs/{rid}")
def delete_run(
    rid: int,
    authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    api_helpers._authorize_run_mutation(
        authorization, s, rid, operation="delete",
    )
    if not service.delete_run(s, rid):
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}


# ---------- Project visibility & members ----------


# ---------- Project Members ----------
