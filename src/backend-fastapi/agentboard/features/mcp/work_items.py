"""MCP helpers for work_items feature (Phase 6 split from mcp_server.py).

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


def _task_list(story_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/stories/{story_id}/tasks", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _task_create(project_id, story_id, title, type, description, spec, priority="medium"):
    return _http("POST", f"/api/stories/{story_id}/tasks",
                 json={"project_id": project_id, "title": title, "type": type,
                       "description": description, "spec": spec, "priority": priority})

def _task_get(task_id):
    return _http("GET", f"/api/tasks/{task_id}")

def _task_update(task_id, fields):
    return _http("PATCH", f"/api/tasks/{task_id}", json=fields)

def _task_append_spec(task_id, text):
    return _http("POST", f"/api/tasks/{task_id}/spec/append", json={"text": text})

def _task_delete(task_id):
    return _http("DELETE", f"/api/tasks/{task_id}")

def _task_status(task_id, status, status_reason=None):
    body = {"status": status}
    if status_reason is not None:
        body["status_reason"] = status_reason
    return _http("PUT", f"/api/tasks/{task_id}/status", json=body)

def _task_search(params):
    clean = {k: v for k, v in params.items() if v is not None}
    resp = _http("GET", "/api/tasks", params=clean)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _task_generated(task_id):
    return _http("POST", f"/api/tasks/{task_id}/generate-subtasks")

def _comment_list(task_id):
    return _http("GET", f"/api/tasks/{task_id}/comments")

def _comment_create(task_id, author, content):
    return _http("POST", f"/api/tasks/{task_id}/comments",
                 json={"author": author, "content": content})

def _comment_delete(comment_id):
    return _http("DELETE", f"/api/comments/{comment_id}")

def _attachment_list(task_id):
    return _http("GET", f"/api/tasks/{task_id}/attachments")

def _attachment_get(attachment_id):
    return _http("GET", f"/api/attachments/{attachment_id}/info")

def _get_task_review_context(task_id):
    return _http("GET", f"/api/tasks/{task_id}/review-context")
