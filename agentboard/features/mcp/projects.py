"""MCP helpers for projects feature (Phase 6 split from mcp_server.py).

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


def _proj_list(limit=None, offset=0):
    # 作用域 = 令牌关联用户的权限（2026-07-29 修正）：
    # - 管理员身份 → /api/projects 全量视图（与 REST API 行为一致）；
    # - 普通用户 → /api/users/me/projects 成员作用域，防止越权浏览全部项目。
    # 防越权的正确边界是"给 MCP 配非管理员 key"（make-mcp-token.py 默认
    # mcp-service 用户），而不是在这里无视 is_admin 一刀切。
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    me = _http("GET", "/api/auth/me")
    if isinstance(me, dict) and me.get("is_admin"):
        resp = _http("GET", "/api/projects", params=params)
    else:
        resp = _http("GET", "/api/users/me/projects", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _proj_create(name, key, description):
    return _http("POST", "/api/projects", json={"name": name, "key": key, "description": description})

def _proj_get(project_id):
    return _http("GET", f"/api/projects/{project_id}")

def _proj_update(project_id, fields):
    return _http("PATCH", f"/api/projects/{project_id}", json=fields)

def _proj_delete(project_id):
    return _http("DELETE", f"/api/projects/{project_id}")

def _epic_list(project_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/projects/{project_id}/epics", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _epic_create(project_id, title, description):
    return _http("POST", f"/api/projects/{project_id}/epics", json={"title": title, "description": description})

def _story_create(epic_id, title, description, needs_design=True):
    return _http("POST", f"/api/epics/{epic_id}/stories",
                 json={"title": title, "description": description,
                       "needs_design": needs_design})

def _story_list(epic_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/epics/{epic_id}/stories", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _epic_get(epic_id):
    return _http("GET", f"/api/epics/{epic_id}")

def _epic_update(epic_id, fields):
    return _http("PATCH", f"/api/epics/{epic_id}", json=fields)

def _epic_delete(epic_id):
    return _http("DELETE", f"/api/epics/{epic_id}")

def _story_get(story_id):
    return _http("GET", f"/api/stories/{story_id}")

def _story_update(story_id, fields):
    return _http("PATCH", f"/api/stories/{story_id}", json=fields)

def _story_delete(story_id):
    return _http("DELETE", f"/api/stories/{story_id}")

def _story_comment_list(story_id):
    return _http("GET", f"/api/stories/{story_id}/comments")

def _story_comment_create(story_id, author, content):
    return _http("POST", f"/api/stories/{story_id}/comments",
                 json={"author": author, "content": content})

def _epic_comment_list(epic_id):
    return _http("GET", f"/api/epics/{epic_id}/comments")

def _epic_comment_create(epic_id, author, content):
    return _http("POST", f"/api/epics/{epic_id}/comments",
                 json={"author": author, "content": content})

def _sprint_list(project_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/projects/{project_id}/sprints", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _sprint_get(sprint_id):
    return _http("GET", f"/api/sprints/{sprint_id}")

def _sprint_create(project_id, title, goal="", start_date=None, end_date=None):
    body = {"title": title, "goal": goal}
    if start_date:
        body["start_date"] = start_date
    if end_date:
        body["end_date"] = end_date
    return _http("POST", f"/api/projects/{project_id}/sprints", json=body)

def _sprint_update(sprint_id, fields):
    return _http("PATCH", f"/api/sprints/{sprint_id}", json=fields)

def _sprint_activate(sprint_id):
    return _http("POST", f"/api/sprints/{sprint_id}/activate")

def _sprint_complete(sprint_id):
    return _http("POST", f"/api/sprints/{sprint_id}/complete")

def _sprint_delete(sprint_id):
    return _http("DELETE", f"/api/sprints/{sprint_id}")

def _sprint_task_list(sprint_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    return _http("GET", f"/api/sprints/{sprint_id}/tasks", params=params)

def _member_list(project_id: int, limit: int = 50, offset: int = 0):
    params = {"limit": limit, "offset": offset}
    return _http("GET", f"/api/projects/{project_id}/members", params=params)

def _member_add(project_id: int, user_id: int | None = None, username: str | None = None, role: str = "member"):
    body = {"role": role}
    if user_id is not None:
        body["user_id"] = user_id
    if username is not None:
        body["username"] = username
    return _http("POST", f"/api/projects/{project_id}/members", json=body)

def _member_remove(project_id: int, user_id: int):
    return _http("DELETE", f"/api/projects/{project_id}/members/{user_id}")

def _member_update_role(project_id: int, user_id: int, role: str):
    return _http("PATCH", f"/api/projects/{project_id}/members/{user_id}", json={"role": role})

def _project_stats(project_id):
    return _http("GET", f"/api/projects/{project_id}/stats")
