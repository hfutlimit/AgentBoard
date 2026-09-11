"""WorkflowRun + WorkflowRunEvent models (Active Workflows Overview, 2026-09-11).

Two tables:

- ``workflow_runs`` — execution instance for a Story (v1 only ``workflow_type='story'``;
  schema left open for future ``schedule`` / ``proposal`` / ``ticket`` / ``deployment``).
  Terminal states (completed / failed / cancelled) are STRICT — ``failed`` is not retryable;
  retrying a terminal workflow creates a new run with ``reopened_from_run_id`` set.

- ``workflow_run_events`` — immutable event stream. Phase + state are decoupled so we
  never end up with ``design_in_review`` / ``qa_changes_requested`` enums.

DB migration: ``m5n6o7p8q9r0_create_workflow_runs_and_events.py``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.models import Base, utc_now


# Status enums duplicated here (the canonical enums live in core.common.enums,
# but workflow_runs is a new feature and we want to keep CHECK constraints
# stable without forcing a global enum addition before slice 1 ships).
WORKFLOW_RUN_STATUSES: tuple[str, ...] = (
    "queued", "running", "waiting", "blocked", "completed", "failed", "cancelled",
)
WORKFLOW_RUN_PHASES: tuple[str, ...] = ("design", "development", "qa")
WORKFLOW_RUN_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

# v1: implementation only accepts 'story', but schema stays open (no CHECK).
WORKFLOW_TYPES_V1: tuple[str, ...] = ("story",)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','waiting','blocked','completed','failed','cancelled')",
            name="ck_workflow_runs_status",
        ),
        CheckConstraint(
            "phase IS NULL OR phase IN ('design','development','qa')",
            name="ck_workflow_runs_phase",
        ),
        Index("ix_workflow_runs_project_status", "project_id", "status", "last_activity_at"),
        Index("ix_workflow_runs_story", "story_id"),
        Index("ix_workflow_runs_schedule", "schedule_id"),
        Index("ix_workflow_runs_reopened_from", "reopened_from_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # v1: only 'story' is written by application code; the column stays VARCHAR
    # (no CHECK) so future workflow types (schedule/proposal/ticket/deployment)
    # can be added without a schema migration.
    workflow_type: Mapped[str] = mapped_column(String(20), nullable=False)
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_schedules.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    current_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True,
    )
    # Reopen / retry association: when a terminal workflow is reopened, the
    # new run points back to the previous one. History of the old run is
    # NEVER mutated (terminal states are immutable).
    reopened_from_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optimistic lock for SignalR push; incremented on every event emit.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False,
    )


class WorkflowRunEvent(Base):
    __tablename__ = "workflow_run_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user','agent','worker','system')",
            name="ck_workflow_events_actor",
        ),
        # Compound index for "latest event of a run" queries (id DESC).
        Index("ix_workflow_events_run_id_desc", "workflow_run_id", "id"),
        Index("ix_workflow_events_task", "task_id"),
        Index("ix_workflow_events_agent_run", "agent_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # MySQL JSON / SQLite JSON: SQLAlchemy Text is the safe cross-dialect default.
    # The application code always passes a JSON-serializable dict.
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False, index=True,
    )


__all__ = [
    "WORKFLOW_RUN_STATUSES",
    "WORKFLOW_RUN_PHASES",
    "WORKFLOW_RUN_TERMINAL_STATUSES",
    "WORKFLOW_TYPES_V1",
    "WorkflowRun",
    "WorkflowRunEvent",
]
