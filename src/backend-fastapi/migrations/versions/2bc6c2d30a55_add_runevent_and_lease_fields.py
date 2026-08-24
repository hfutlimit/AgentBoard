"""Add RunEvent and lease fields to AgentRun records.

Revision ID: 2bc6c2d30a55
Revises: g2h3i4j5k6l7
Create Date: 2026-08-23 10:49:19.496707
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2bc6c2d30a55"
down_revision: Union[str, None] = "g2h3i4j5k6l7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_events_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_run_events_run_id"), ["run_id"], unique=False)

    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lease_worker_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_agent_runs_lease_expires_at"), ["lease_expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_runs_lease_worker_id"), ["lease_worker_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_runs_lease_worker_id"))
        batch_op.drop_index(batch_op.f("ix_agent_runs_lease_expires_at"))
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_worker_id")

    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_events_run_id"))
        batch_op.drop_index(batch_op.f("ix_agent_run_events_created_at"))

    op.drop_table("agent_run_events")
