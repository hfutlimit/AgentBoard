"""agent_runs progress tracking: in-flight heartbeat + stale detection

Revision ID: 0a0b0c0d0e0f
Revises: n2o3p4q5r6s7
Create Date: 2026-09-14

Problem 1 of 2026-09-14: Agent in-flight resilience.

Background
----------
Today, once ``agent_runs.status='running'`` is set, the only safety net is
the **claim lease** on the parent Task (default 1800s = 30 min) plus
``reclaim_stale_tasks`` which **reverts the Task to todo** when the lease
expires. There is no way for an agent to say "I'm still alive and working
on this, please don't kill me" mid-execution. There is also no concept of
**soft takeover** — if the original agent is genuinely dead, the only
recourse is "abandon the run, hope someone re-claims via the pool."

This migration adds the storage layer for the new resilience flow:

- ``agent_runs.last_progress_at`` (DateTime, nullable) — last time the
  agent called ``report_task_progress`` for this run. Stays fresh as long
  as the agent is alive and working.
- ``agent_runs.last_progress_note`` (String(500), nullable) — human-readable
  status string the agent wants reviewers / next agent to see (e.g.
  "wrote 3 of 5 migrations, currently on c1d2e3"). Bounded to 500 chars
  so the column stays cheap.
- ``agent_runs.is_stale`` (Boolean, default False) — server-side marker
  flipped to True when ``scan_stale_agent_runs`` observes a long gap
  between ``now`` and ``last_progress_at``. UI surface uses this to
  yellow-flag the run in the active-workflows panel. Distinct from
  ``status='failed'`` because the run is still being given a chance to
  recover (soft takeover) or be replaced.
- Compound index on (status, last_progress_at) — supports the
  "find all running runs whose last_progress_at is older than X" scan
  in O(matches) instead of full table.

Down-migration drops the columns and the index. Existing rows default
to NULL / False, so the migration is forward- and backward-safe.

Revision ID note: avoid ``c1d2e3f4a5b6`` / ``c7d8e9f0a1b2`` /
``a1b2c3d4e5f6`` / ``b1c2d3e4f5a6`` / ``a9b8c7d6e5f4`` which already
exist on remote main for unrelated features (agent_run progress,
learning RAG, project archive, agent behavior configs). Picked
``0a0b0c0d0e0f`` to live in a numeric-leading namespace that's
definitely not in the existing 12-char alpha cluster.
"""
from alembic import op
import sqlalchemy as sa


revision = "0a0b0c0d0e0f"
down_revision = "n2o3p4q5r6s7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("last_progress_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_progress_note", sa.String(500), nullable=True))
        batch.add_column(
            sa.Column(
                "is_stale",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.create_index(
            "ix_agent_runs_status_last_progress",
            ["status", "last_progress_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_status_last_progress")
        batch.drop_column("is_stale")
        batch.drop_column("last_progress_note")
        batch.drop_column("last_progress_at")
