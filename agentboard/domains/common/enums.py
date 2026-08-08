from enum import StrEnum


class ItemType(StrEnum):
    TASK = "task"
    BUG = "bug"
    TEST_EXECUTION = "test_execution"


class Status(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    VERIFYING = "verifying"
    DONE = "done"
    BLOCKED = "blocked"
    # 设计评审流（Epic 123）：needs_design=true 的 Story 下 Task 走设计段 + 最终评审
    IN_DESIGN = "in_design"
    DESIGN_PENDING_REVIEW = "design_pending_review"
    DESIGN_REVIEW_APPROVED = "design_review_approved"
    FINAL_REVIEW = "final_review"


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
    CANCELLED = "cancelled"


ALL_TYPES = [ItemType.TASK, ItemType.BUG, ItemType.TEST_EXECUTION]
ALL_STATUSES = list(Status)
ALL_PRIORITIES = list(Priority)
ALL_SPRINT_STATUSES = list(SprintStatus)
ALL_SCHEDULE_TYPES = list(ScheduleType)
ALL_RUN_STATUSES = list(RunStatus)
