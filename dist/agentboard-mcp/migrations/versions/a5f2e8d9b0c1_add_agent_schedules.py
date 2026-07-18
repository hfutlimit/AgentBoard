"""add agent_schedules and agent_runs tables

Revision ID: a5f2e8d9b0c1
Revises: 9e4f1d5c8a3b
Create Date: 2026-07-12 17:40:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "a5f2e8d9b0c1"
down_revision: Union[str, None] = "9e4f1d5c8a3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("schedule_type", sa.String(10), nullable=False, server_default="cron"),
        sa.Column("cron_expr", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("agent_schedules.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("agent_schedules")
