"""Best-effort bridge from FastAPI workflow events to the SignalR BFF."""
from __future__ import annotations

import logging
import os

import httpx


log = logging.getLogger(__name__)


def notify_proposal_questions(*, proposal_id: int, project_id: int, round_no: int) -> None:
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
