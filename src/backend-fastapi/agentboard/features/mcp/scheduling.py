"""MCP helpers for scheduling feature (Phase 6 split from mcp_server.py).

Each function is a thin wrapper around the AgentBoard REST API, used by the
MCP tool functions defined in agentboard/mcp_server.py. The leading underscore
in `_xxx_yyy` is the original convention from mcp_server.py (private to MCP).

The MCP tool functions are kept in mcp_server.py for now — Phase 6b will move
them to a registry-based auto-generation pattern.
"""
from __future__ import annotations
import os
from typing import Any
import httpx

from .shared import _current_token, _http  # Phase 6: shared HTTP helpers


def _schedule_list(project_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    return _http("GET", f"/api/projects/{project_id}/schedules", params=params)

def _schedule_get(schedule_id):
    return _http("GET", f"/api/schedules/{schedule_id}")

def _schedule_create(project_id, title, schedule_type="cron", cron_expr=None,
                     agent=None, task_id=None, task_priority=None,
                     task_type=None, epic_id=None):
    body = {"title": title, "schedule_type": schedule_type}
    if cron_expr:
        body["cron_expr"] = cron_expr
    # Story 106：绑定松绑字段（None 不传 = 不设置）
    for k, v in dict(agent=agent, task_id=task_id, task_priority=task_priority,
                     task_type=task_type, epic_id=epic_id).items():
        if v is not None:
            body[k] = v
    return _http("POST", f"/api/projects/{project_id}/schedules", json=body)

def _schedule_update(schedule_id, fields):
    return _http("PATCH", f"/api/schedules/{schedule_id}", json=fields)

def _schedule_delete(schedule_id):
    return _http("DELETE", f"/api/schedules/{schedule_id}")

def _run_create(schedule_id, task_id=None, idempotency_key=None):
    body = {}
    if task_id is not None:
        body["task_id"] = task_id
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return _http("POST", f"/api/schedules/{schedule_id}/runs", json=body)

def _run_list(schedule_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    return _http("GET", f"/api/schedules/{schedule_id}/runs", params=params)

def _run_get(run_id):
    return _http("GET", f"/api/runs/{run_id}")

def _run_update(run_id, fields):
    return _http("PATCH", f"/api/runs/{run_id}", json=fields)

def _run_delete(run_id):
    return _http("DELETE", f"/api/runs/{run_id}")


def _get_or_create_claim_schedule(project_id, task_id):
    """Return a usable schedule for manual claims without relying on id=1."""
    schedules = _http("GET", f"/api/projects/{project_id}/schedules")
    if isinstance(schedules, dict) and schedules.get("error"):
        return schedules
    if isinstance(schedules, list):
        for schedule in schedules:
            if isinstance(schedule, dict) and schedule.get("id") is not None:
                return schedule

    return _http(
        "POST",
        f"/api/projects/{project_id}/schedules",
        json={
            "title": f"Manual task claim {task_id}",
            "schedule_type": "once",
        },
    )

def _agent_claim_task(task_id, agent_name="agent"):
    """Agent 认领任务（Epic 118 并发护栏版）：
    - 任务非 backlog/todo（已被认领或已结束）→ 返回明确错误，不创建 Run、不改状态；
    - 同一 agent 对同一 task 已有 active Run（pending/running）→ 幂等复用；
    - 空闲任务 → 创建 Run 并推进 in_progress。
    """
    import uuid
    # 获取 task 详情
    t = _http("GET", f"/api/tasks/{task_id}")
    if "error" in t:
        return t
    status = t.get("status")
    # 并发护栏：任务已被认领（in_progress 等）或已结束（done）时拒绝重复认领，
    # 避免多 Agent 并行时重复创建 Run / 重复推进状态。
    if status not in ("backlog", "todo"):
        return {
            "error": f"task {task_id} already claimed or not claimable (status={status})",
            "task": t,
            "run": None,
        }
    # Run 幂等复用：同一 task 已有 active Run（pending/running）则复用，不新建
    schedule = _get_or_create_claim_schedule(t.get("project_id"), task_id)
    if not isinstance(schedule, dict) or schedule.get("error"):
        return {"error": schedule.get("error", "unable to resolve claim schedule"), "task": t, "run": None}
    schedule_id = schedule.get("id")
    if not isinstance(schedule_id, int):
        return {"error": "claim schedule did not contain an id", "task": t, "run": None}

    runs = _http("GET", f"/api/schedules/{schedule_id}/runs")
    if isinstance(runs, list):
        for r in runs:
            if r.get("task_id") == task_id and r.get("status") in ("pending", "running"):
                _http("PUT", f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
                t = _http("GET", f"/api/tasks/{task_id}")
                return {"run": r, "task": t, "schedule": None, "reused": True}
    # 创建 run（schedule 1 为手动触发占位，历史约定保持不变）
    idempotency_key = f"{agent_name}-{task_id}-{uuid.uuid4().hex[:8]}"
    run = _http("POST", f"/api/schedules/{schedule_id}/runs",
                json={"task_id": task_id, "idempotency_key": idempotency_key})
    if "error" in run:
        return {"error": run["error"], "task": t, "run": None}
    # 同步任务状态
    _http("PUT", f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
    t = _http("GET", f"/api/tasks/{task_id}")
    return {"run": run, "task": t, "schedule": None}

def _agent_heartbeat(run_id, status="running"):
    fields = {"status": status}
    return _http("PATCH", f"/api/runs/{run_id}", json=fields)

def _agent_complete_run(run_id, output, status="success", error_message=None):
    fields = {"status": status, "output": output}
    if error_message:
        fields["error_message"] = error_message
    return _http("PATCH", f"/api/runs/{run_id}", json=fields)

def _run_event_create(run_id, event_type, payload):
    return _http("POST", f"/api/agent-runs/{run_id}/events", json={"event_type": event_type, "payload": payload})
