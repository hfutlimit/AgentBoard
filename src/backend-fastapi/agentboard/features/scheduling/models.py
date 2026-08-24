from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.enums import RunStatus, ScheduleType
from ...core.common.models import Base, utc_now


class AgentSchedule(Base):
    __tablename__ = "agent_schedules"
    __table_args__ = (CheckConstraint(
        "schedule_type IN ('once','cron')", name="ck_schedules_type",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(10), default=ScheduleType.CRON)
    cron_expr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Story 106：绑定松绑 —— agent 维度 + 任务绑定 + 可选筛选（全部 nullable，旧行零迁移成本）
    agent: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    task_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    epic_id: Mapped[int | None] = mapped_column(ForeignKey("epics.id"), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (CheckConstraint(
        "status IN ('pending','running','success','failed','cancelled')", name="ck_runs_status",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("agent_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    agent_registry_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_assignments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Execution-time snapshot: schedule/Agent configuration may change later,
    # but historical records must keep the agent and model actually selected.
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.PENDING)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lease_worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class RunEvent(Base):
    __tablename__ = "agent_run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    agent_registry_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_username_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_prefix_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_ref_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class TaskAssignment(Base):
    """Immutable task ownership attempt with one cross-database active slot."""

    __tablename__ = "task_assignments"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "active_slot", name="uq_task_assignment_active_slot"
        ),
        CheckConstraint(
            "source IN ('claim','arbitration','schedule','manual','worker')",
            name="ck_task_assignment_source",
        ),
        CheckConstraint(
            "status IN ('active','completed','released','cancelled')",
            name="ck_task_assignment_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_registry_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    active_slot: Mapped[str | None] = mapped_column(String(10), nullable=True, default="active")
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_reason: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskApplication(Base):
    """Agent application for an arbitrated task."""

    __tablename__ = "task_applications"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "agent_registry_id", name="uq_task_application_agent"
        ),
        CheckConstraint(
            "status IN ('pending','accepted','rejected','withdrawn')",
            name="ck_task_application_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_registry_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# Review 流程常量（从原 service.py 715-720 行搬迁）
REVIEW_MODE_SINGLE = "single"      # 1 名 reviewer，approve 即通过（默认）
REVIEW_MODE_MAJORITY = "majority"  # N 人投票，达法定票数按多数决
DEFAULT_REVIEW_QUORUM = 3          # 法定票数
MAX_REVIEW_ROUNDS = 5              # 与 Proposal max_rounds 对齐
DEFAULT_REVIEW_TIMEOUT_MINUTES = 30  # 评审超时（30 分钟）
DEFAULT_TIMEOUT_SCAN_BATCH = 20    # 每次扫描批大小
