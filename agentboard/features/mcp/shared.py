"""Shared MCP HTTP helpers (Phase 6 split from mcp_server.py).

`_http` / `_current_token` were defined at the top of mcp_server.py and used
by every feature helper. They live here so each features/mcp/<X>.py module
can import them without circular references.

Note: `_http` resolves `httpx` via mcp_server's namespace, NOT this module.
This is intentional: legacy smoke tests monkey-patch
`agentboard.mcp_server.httpx` to substitute a starlette TestClient, and
we want that patch to propagate. We fall back to the real `httpx` module
if mcp_server isn't loaded yet.
"""
from __future__ import annotations
import os
import sys
import httpx as _httpx_real
from fastmcp.server.dependencies import get_access_token

API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:58124")


def _current_token():
    try:
        access = get_access_token()
    except RuntimeError:
        access = None
    return access.token if access else os.getenv("AGENTBOARD_MCP_TOKEN")


def _http(method, path, **kw):
    # Look up httpx from mcp_server first (so tests can monkey-patch it),
    # fall back to the real module if mcp_server isn't loaded yet.
    mcp = sys.modules.get("agentboard.mcp_server")
    httpx = getattr(mcp, "httpx", _httpx_real) if mcp else _httpx_real
    # API_URL 同样优先取 mcp_server 的属性：拆分前 _http 定义在 mcp_server.py，
    # 既有测试统一用 `mcp_server.API_URL = <base>` 把 MCP 工具指向测试栈；
    # Phase 6 拆分后该覆盖必须继续生效（生产未覆盖时与 shared.API_URL 同源）。
    api_url = getattr(mcp, "API_URL", None) or API_URL
    headers = dict(kw.pop("headers", {}) or {})
    token = _current_token()
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(base_url=api_url, timeout=15) as c:
        r = c.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                return {"error": r.json().get("detail", r.text)}
            except Exception:
                return {"error": r.text}
        return r.json() if r.content else {"ok": True}
