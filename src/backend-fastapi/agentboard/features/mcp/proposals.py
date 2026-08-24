"""MCP helpers for proposals feature (Phase 6 split from mcp_server.py).

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


def _proposal_status(proposal_id: int, status: str, error: str | None = None) -> dict:
    """提案状态机流转（私有 helper，供 claim / finalize / fail 复用）。"""
    body: dict = {"status": status}
    if error is not None:
        body["error"] = error
    return _http("PUT", f"/api/proposals/{proposal_id}/status", json=body)

def _proposal_replay(proposal: dict, rounds: list) -> dict:
    """把提案正文与全部历史轮次压成一份可直接重放的上下文。

    ``history`` 为按轮次正序的扁平问答（含 unsure 标记），Agent 只要读这一份
    就能无状态地续接澄清——这正是全量重放策略的落点。
    ``open_questions`` 单独列出尚未作答的问题，便于 Agent 判断是否还在等人。
    """
    history: list[dict] = []
    open_questions: list[dict] = []
    for r in rounds or []:
        for q in r.get("questions", []) or []:
            answered = bool(q.get("answered_at"))
            item = {
                "round": r.get("round_no"),
                "question_id": q.get("id"),
                "seq": q.get("seq"),
                "question": q.get("question"),
                "answer": q.get("answer") or "",
                "unsure": bool(q.get("unsure")),
                "answered": answered,
            }
            history.append(item)
            if not answered:
                open_questions.append(item)
    return {
        "proposal_id": proposal.get("id"),
        "project_id": proposal.get("project_id"),
        "title": proposal.get("title"),
        "content": proposal.get("content") or "",
        "status": proposal.get("status"),
        "current_round": proposal.get("current_round", 0),
        "converged_spec": proposal.get("converged_spec") or "",
        "error": proposal.get("error") or "",
        "rounds": rounds or [],
        "history": history,
        "open_questions": open_questions,
        "answered_count": sum(1 for h in history if h["answered"]),
        "total_questions": len(history),
    }
