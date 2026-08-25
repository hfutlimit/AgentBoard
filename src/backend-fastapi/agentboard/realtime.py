"""Best-effort bridge from FastAPI workflow events to the SignalR BFF.

P2-10: the previous version of ``notify_proposal_questions`` issued a
synchronous ``httpx.post`` and blocked the API request for up to 2
seconds. We now expose ``schedule_proposal_questions`` which the
FastAPI handler hands to ``BackgroundTasks``, so persistence is the
last thing the proposal endpoint does before it returns to the client
and the cross-process signal goes out on a worker thread.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import BackgroundTasks


log = logging.getLogger(__name__)


def _send_proposal_questions_notification(*, proposal_id: int, project_id: int, round_no: int) -> None:
    """Notify connected goal-mode clients without blocking proposal persistence.

    The notification contains identifiers only; the browser re-reads the
    proposal through its authenticated REST API after receiving the event.
    """
    url = os.getenv("AGENTBOARD_REALTIME_NOTIFY_URL", "").strip()
    key = os.getenv("AGENTBOARD_REALTIME_INTERNAL_KEY", "").strip()
    if not url or not key:
        return
    payload = {
        "proposal_id": proposal_id,
        "project_id": project_id,
        "round": round_no,
        "workflow": "goal",
        "event": "proposal.questions_raised",
    }
    try:
        response = httpx.post(
            url,
            headers={"X-AgentBoard-Realtime-Key": key},
            json=payload,
            timeout=2.0,
        )
        if response.is_error:
            log.warning("SignalR proposal notification failed with HTTP %s", response.status_code)
    except Exception:
        log.warning("SignalR proposal notification failed", exc_info=True)


def schedule_proposal_questions(
    background: Optional[BackgroundTasks],
    *,
    proposal_id: int,
    project_id: int,
    round_no: int,
) -> None:
    """Hand the SignalR bridge call to FastAPI's BackgroundTasks.

    When called without a ``BackgroundTasks`` (e.g. from background
    jobs that have no request context) we fall back to running the
    send synchronously — that is still bounded by the 2-second httpx
    timeout and never throws.
    """
    if background is None:
        _send_proposal_questions_notification(
            proposal_id=proposal_id,
            project_id=project_id,
            round_no=round_no,
        )
        return
    background.add_task(
        _send_proposal_questions_notification,
        proposal_id=proposal_id,
        project_id=project_id,
        round_no=round_no,
    )


def notify_proposal_questions(*, proposal_id: int, project_id: int, round_no: int) -> None:
    """Synchronous entry point — kept for callers outside the request
    scope (e.g. worker scripts). Prefer ``schedule_proposal_questions``
    from FastAPI handlers.
    """
    _send_proposal_questions_notification(
        proposal_id=proposal_id,
        project_id=project_id,
        round_no=round_no,
    )
