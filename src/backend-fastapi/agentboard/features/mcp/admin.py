"""MCP helpers for admin feature (Phase 6 split from mcp_server.py).

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


def _admin_list_users(limit: int = 50, offset: int = 0):
    return _http("GET", "/api/admin/users", params={"limit": limit, "offset": offset})

def _admin_update_user(user_id: int, is_admin: bool):
    return _http("PATCH", f"/api/admin/users/{user_id}", json={"is_admin": is_admin})

def _admin_list_projects(limit: int = 50, offset: int = 0):
    return _http("GET", "/api/admin/projects", params={"limit": limit, "offset": offset})

def _admin_delete_project(project_id: int):
    return _http("DELETE", f"/api/admin/projects/{project_id}")
