"""Worker handlers package (Unified Execution Model).

Defines the BaseWorkHandler protocol and concrete handlers.
The WorkerCoordinator instantiates each handler and routes work by WorkType.
"""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .clarify import ClarifyHandler  # noqa: F401
from .story import StoryHandler, build_story_prompt, build_task_prompt  # noqa: F401
from .ticket import TicketHandler, build_ticket_prompt  # noqa: F401
from .review import ReviewHandler, OwnerResponseHandler  # noqa: F401
from ..contract import WorkType

__all__ = [
    "BaseWorkHandler",
    "Handler",
    "ClarifyHandler",
    "StoryHandler",
    "TicketHandler",
    "ReviewHandler",
    "OwnerResponseHandler",
    "build_story_prompt",
    "build_task_prompt",
    "build_ticket_prompt",
    "build_handlers",
    "build_work_type_registry",
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


def build_work_type_registry(client, config) -> dict[WorkType, BaseWorkHandler]:
    """Construct all built-in Handlers, return ``{WorkType: handler}`` registry."""
    handlers = [
        ClarifyHandler(client, config),
        TicketHandler(client, config),
        StoryHandler(client, config),
        ReviewHandler(client, config),
        OwnerResponseHandler(client, config),
    ]
    return {h.work_type: h for h in handlers}
