"""MCP helpers for auth feature (Phase 6 split from mcp_server.py).

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


def _auth_register(username, password):
    return _http("POST", "/api/auth/register", json={"username": username, "password": password})

def _auth_login(username, password):
    return _http("POST", "/api/auth/login", json={"username": username, "password": password})

def _auth_me(token):
    return _http("GET", "/api/auth/me", headers={"Authorization": f"Bearer {token}"})
