"""MCP helpers for documents feature (Phase 6 split from mcp_server.py).

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


def _doc_create(project_id, title, content="", type="plan", status="draft",
                epic_id=None, story_id=None, author_id=None, folder_id=None):
    body = {"project_id": project_id, "title": title, "content": content,
            "type": type, "status": status}
    if epic_id is not None:
        body["epic_id"] = epic_id
    if story_id is not None:
        body["story_id"] = story_id
    if author_id is not None:
        body["author_id"] = author_id
    if folder_id is not None:
        body["folder_id"] = folder_id
    return _http("POST", "/api/documents", json=body)

def _doc_get(document_id):
    return _http("GET", f"/api/documents/{document_id}")

def _doc_list(project_id=None, type=None, status=None, q=None, limit=None, offset=0,
              folder_id=None, author_id=None, epic_id=None, story_id=None, sort=None):
    params = {"offset": offset}
    if project_id is not None:
        params["project_id"] = project_id
    if type is not None:
        params["type"] = type
    if status is not None:
        params["status"] = status
    if q is not None:
        params["q"] = q
    if folder_id is not None:
        params["folder_id"] = folder_id
    if author_id is not None:
        params["author_id"] = author_id
    if epic_id is not None:
        params["epic_id"] = epic_id
    if story_id is not None:
        params["story_id"] = story_id
    if sort is not None:
        params["sort"] = sort
    if limit is not None:
        params["limit"] = limit
    return _http("GET", "/api/documents", params=params)

def _doc_update(document_id, fields):
    return _http("PATCH", f"/api/documents/{document_id}", json=fields)

def _doc_delete(document_id):
    return _http("DELETE", f"/api/documents/{document_id}")

def _doc_status(document_id, status):
    return _http("PUT", f"/api/documents/{document_id}/status", json={"status": status})

def _doc_comment_create(document_id, author, content, author_id=None):
    body = {"author": author, "content": content}
    if author_id is not None:
        body["author_id"] = author_id
    return _http("POST", f"/api/documents/{document_id}/comments", json=body)

def _doc_comment_list(document_id):
    return _http("GET", f"/api/documents/{document_id}/comments")

def _doc_comment_update(comment_id, content, author):
    return _http("PATCH", f"/api/document-comments/{comment_id}",
                 json={"content": content, "author": author})

def _doc_comment_delete(comment_id):
    return _http("DELETE", f"/api/document-comments/{comment_id}")

def _folder_list(project_id=None):
    params = {}
    if project_id is not None:
        params["project_id"] = project_id
    return _http("GET", "/api/document-folders", params=params)

def _folder_create(project_id, name, parent_id=None):
    body = {"project_id": project_id, "name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    return _http("POST", "/api/document-folders", json=body)

def _folder_update(folder_id, fields):
    return _http("PATCH", f"/api/document-folders/{folder_id}", json=fields)

def _folder_delete(folder_id):
    return _http("DELETE", f"/api/document-folders/{folder_id}")

# 记忆文档 title 约定（分层零 DB 变更，title 前缀隔离）：
#   - 项目级：title = "项目记忆" —— 团队规范 / 约定 / 踩坑，所有 Agent 共享；
#   - Agent 级：title = "Agent 记忆 · {agent}" —— 某 Agent 个性 / 擅长领域，按 agent 隔离。
# 这两个常量的唯一真源在这里（_memory_title 的消费方）；mcp_server.py 里的同名
# 名字只是别名引用。2026-09-02 修复：Phase 6b 把 _memory_title 拆进来时常量
# 留在了 mcp_server.py，导致 append/get 记忆时 NameError。
_MEMORY_PROJECT_TITLE = "项目记忆"
_MEMORY_AGENT_PREFIX = "Agent 记忆 · "

def _memory_title(agent: str | None) -> str:
    return f"{_MEMORY_AGENT_PREFIX}{agent}" if agent else _MEMORY_PROJECT_TITLE
