"""Compatibility facade for domain models.

New code should import from ``agentboard.domains.<domain>``. This module keeps
the existing public imports stable while the service and API layers migrate.
"""

from .domains.common.enums import (
    ALL_PRIORITIES,
    ALL_RUN_STATUSES,
    ALL_SCHEDULE_TYPES,
    ALL_SPRINT_STATUSES,
    ALL_STATUSES,
    ALL_TYPES,
    ItemType,
    Priority,
    RunStatus,
    ScheduleType,
    SprintStatus,
    Status,
)
from .domains.common.models import Base, utc_now as _now
from .domains.identity.models import ApiKey, Notification, User
from .domains.projects.models import Epic, Project, ProjectMember, Sprint, Story
from .domains.scheduling.models import AgentRun, AgentSchedule
from .domains.work_items.models import Attachment, Comment, Task

__all__ = [
    "ALL_PRIORITIES", "ALL_RUN_STATUSES", "ALL_SCHEDULE_TYPES",
    "ALL_SPRINT_STATUSES", "ALL_STATUSES", "ALL_TYPES", "AgentRun",
    "AgentSchedule", "ApiKey", "Attachment", "Base", "Comment", "Epic", "ItemType",
    "Notification", "Priority", "Project", "ProjectMember", "RunStatus",
    "ScheduleType", "Sprint", "SprintStatus", "Status", "Story", "Task",
    "User",
]
