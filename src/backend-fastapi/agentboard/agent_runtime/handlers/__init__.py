"""Worker handlers package (Phase 7 moved from agentboard/worker/handlers/).

Defines the Handler protocol and the concrete handlers used by the Worker
main loop. The Worker instantiates each handler and routes work by `name`.
"""
from __future__ import annotations
from .base import *  # noqa: F401,F403
from .clarify import ClarifyHandler  # noqa: F401
from .story import StoryHandler, build_story_prompt, build_task_prompt  # noqa: F401
from .ticket import TicketHandler, build_ticket_prompt  # noqa: F401
from .review import ReviewHandler, OwnerResponseHandler  # noqa: F401

__all__ = [
    "ClarifyHandler",
    "StoryHandler",
    "TicketHandler",
    "ReviewHandler",
    "OwnerResponseHandler",
    "build_story_prompt",
    "build_task_prompt",
    "build_ticket_prompt",
    "build_handlers",
]


def build_handlers(client, config):
    """Construct all built-in Handlers, return ``{name: handler}`` routing table."""
    return {
        h.name: h
        for h in (
            ClarifyHandler(client, config),
            TicketHandler(client, config),
            StoryHandler(client, config),
            ReviewHandler(client, config),
            OwnerResponseHandler(client, config),
        )
    }
