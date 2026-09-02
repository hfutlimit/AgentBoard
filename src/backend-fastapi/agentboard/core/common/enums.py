from enum import StrEnum


class ItemType(StrEnum):
    """任务类型（4 值，Story 265 收敛）。"""
    # 开发任务（替换旧 'task'）
    DEV = "dev"
    BUG = "bug"
    # 测试执行（替换旧 'test_execution'）
    QA = "qa"
    # 设计任务
    DESIGN = "design"


class Status(StrEnum):
    """任务状态（5 值，Story 265 收敛）。

    设计评审专用段（in_design / design_pending_review / design_review_approved）
    与最终评审（final_review）已下线，统一归并到通用 5 状态流：
      todo → in_progress → in_review → done
    verifying / final_review 等历史阶段视作 in_progress 的子状态，全部回填。
    """
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    BLOCKED = "blocked"


class StatusReason(StrEnum):
    """状态原因枚举（Story 265 新增；Review 2026-08-26 加 MANUAL_OVERRIDE）。

    - done 状态必填：`completed` / `withdrawn` / `manual_override`（admin 显式强制完成）
    - blocked 状态必填：`blocked_by_other_ticket` / `pending_requirement_change` /
      `out_of_scope` / `duplicate` / `legacy`（仅迁移历史数据使用）
    - 其他状态可选（通常为空）。
    """
    # done
    COMPLETED = "completed"
    WITHDRAWN = "withdrawn"
    # Review 2026-08-26 P1 #3：admin 显式 force_complete_task 路径专用
    MANUAL_OVERRIDE = "manual_override"
    # blocked
    BLOCKED_BY_OTHER_TICKET = "blocked_by_other_ticket"
    PENDING_REQUIREMENT_CHANGE = "pending_requirement_change"
    OUT_OF_SCOPE = "out_of_scope"
    DUPLICATE = "duplicate"
    # T3.1：调度候选不足（owner 名下无在线可执行 agent）。与「人工判定」类
    # blocked 原因不同，这个原因会被 T3.2 解锁钩子**自动恢复**（agent 上线
    # → 按 previous_status 回到原状态），所以它必须能跟人工 blocked 区分开。
    INSUFFICIENT_AGENTS = "insufficient_agents"
    # 迁移专用：历史 blocked 数据无明确原因，标记为遗留（Story 265 migration backfill）
    LEGACY = "legacy"


# 各状态允许的 status_reason 取值（Story 265）
STATUS_REASONS_BY_STATUS: dict[str, set[str]] = {
    Status.DONE: {
        StatusReason.COMPLETED,
        StatusReason.WITHDRAWN,
        StatusReason.MANUAL_OVERRIDE,  # Review 2026-08-26 P1 #3
    },
    Status.BLOCKED: {
        StatusReason.BLOCKED_BY_OTHER_TICKET,
        StatusReason.PENDING_REQUIREMENT_CHANGE,
        StatusReason.OUT_OF_SCOPE,
        StatusReason.DUPLICATE,
        StatusReason.INSUFFICIENT_AGENTS,  # T3.1：调度候选不足（可自动解锁）
        StatusReason.LEGACY,  # 迁移遗留数据专用
    },
}


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


ALL_TYPES = [ItemType.DEV, ItemType.BUG, ItemType.QA, ItemType.DESIGN]
ALL_STATUSES = list(Status)
ALL_STATUS_REASONS = list(StatusReason)
ALL_PRIORITIES = list(Priority)
ALL_SPRINT_STATUSES = list(SprintStatus)
ALL_SCHEDULE_TYPES = list(ScheduleType)
ALL_RUN_STATUSES = list(RunStatus)
