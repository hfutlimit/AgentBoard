"""MCP helpers for notifications feature (Phase 6 split from mcp_server.py).

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


def _notification_list(limit: int = 20, offset: int = 0, unread_only: bool = False):
    params = {"limit": limit, "offset": offset, "unread_only": unread_only}
    return _http("GET", "/api/notifications", params=params)

def _notification_unread_count():
    return _http("GET", "/api/notifications/unread-count")

def _notification_mark_read(notification_id: int):
    return _http("POST", f"/api/notifications/{notification_id}/read")

def _notification_mark_all_read():
    return _http("POST", "/api/notifications/read-all")

def _notification_delete(notification_id: int):
    return _http("DELETE", f"/api/notifications/{notification_id}")
