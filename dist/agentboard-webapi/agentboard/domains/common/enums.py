from enum import StrEnum


class ItemType(StrEnum):
    TASK = "task"
    BUG = "bug"


class Status(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    VERIFYING = "verifying"
    DONE = "done"


class Priority(StrEnum):
    HIGHEST = "highest"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    LOWEST = "lowest"


class SprintStatus(StrEnum):
    PLANNING = "planning"
    ACTIVE = "active"
    COMPLETED = "completed"


class ScheduleType(StrEnum):
    ONCE = "once"
    CRON = "cron"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


ALL_TYPES = [ItemType.TASK, ItemType.BUG]
ALL_STATUSES = list(Status)
ALL_PRIORITIES = list(Priority)
ALL_SPRINT_STATUSES = list(SprintStatus)
ALL_SCHEDULE_TYPES = list(ScheduleType)
ALL_RUN_STATUSES = list(RunStatus)
