"""workflow_runs + workflow_run_events tables (Active Workflows Overview slice 1, 2026-09-11).

Two new tables for the AI Team Live Operations Dashboard:

- ``workflow_runs`` — execution instance for a Story (v1 only ``workflow_type='story'``;
  schema stays open (no CHECK on ``workflow_type``) so future types
  (schedule/proposal/ticket/deployment) can land without a schema migration.

- ``workflow_run_events`` — immutable event stream. Phase + state are decoupled
  to avoid ``design_in_review`` / ``qa_changes_requested`` enum explosions.

Key invariants:
- ``failed`` is a STRICT terminal status; there is no ``failed -> running``
  path. Retrying a terminal workflow creates a new run + ``reopened_from_run_id``.
- Phase transitions follow the explicit graph
  ``design -> development -> qa`` plus ``qa -> development`` (rework).
  Arbitrary phase mutation is prohibited at the application layer.
"""
from alembic import op
import sqlalchemy as sa


revision = "m5n6o7p8q9r0"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        # v1: only 'story' is written by application code, but the column stays
        # VARCHAR with no CHECK so future types can be added without a migration.
        sa.Column("workflow_type", sa.String(length=20), nullable=False),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("agent_schedules.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued",
        ),
        sa.Column("phase", sa.String(length=20), nullable=True),
        sa.Column("current_task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "reopened_from_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting','blocked','completed','failed','cancelled')",
            name="ck_workflow_runs_status",
        ),
        sa.CheckConstraint(
            "phase IS NULL OR phase IN ('design','development','qa')",
            name="ck_workflow_runs_phase",
        ),
        # workflow_type: intentionally NO CHECK constraint (schema extensibility).
    )
    op.create_index(
        "ix_workflow_runs_project_status",
        "workflow_runs",
        ["project_id", "status", "last_activity_at"],
    )
    op.create_index("ix_workflow_runs_story", "workflow_runs", ["story_id"])
    op.create_index("ix_workflow_runs_schedule", "workflow_runs", ["schedule_id"])
    op.create_index(
        "ix_workflow_runs_reopened_from",
        "workflow_runs",
        ["reopened_from_run_id"],
    )

    op.create_table(
        "workflow_run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=True),
        # payload stored as TEXT; application layer always serializes JSON.
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint(
            "actor_type IN ('user','agent','worker','system')",
            name="ck_workflow_events_actor",
        ),
    )
    op.create_index(
        "ix_workflow_events_run_id_desc",
        "workflow_run_events",
        ["workflow_run_id", "id"],
    )
    op.create_index("ix_workflow_events_task", "workflow_run_events", ["task_id"])
    op.create_index("ix_workflow_events_agent_run", "workflow_run_events", ["agent_run_id"])
    op.create_index("ix_workflow_events_created_at", "workflow_run_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_workflow_events_created_at", table_name="workflow_run_events")
    op.drop_index("ix_workflow_events_agent_run", table_name="workflow_run_events")
    op.drop_index("ix_workflow_events_task", table_name="workflow_run_events")
    op.drop_index("ix_workflow_events_run_id_desc", table_name="workflow_run_events")
    op.drop_table("workflow_run_events")

    op.drop_index("ix_workflow_runs_reopened_from", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_schedule", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_story", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_status", table_name="workflow_runs")
    op.drop_table("workflow_runs")
