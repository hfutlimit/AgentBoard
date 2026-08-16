"""task_outcome 表（Epic 140 切片 1）

Revision ID: a2b3c4d5e6f7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-16

- 新增 task_outcome 表：每个完成任务的 L1/L2 过程指标 + 复合分。
- agent_id/task_type/project_id 三键可多维聚合（leaderboard）。
"""
from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "a1b2c3d4e5f6"


def upgrade() -> None:
    op.create_table(
        "task_outcome",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("task_type", sa.String(10), nullable=False, server_default="dev"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("judge_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_task_outcome_score"),
    )
    op.create_index("ix_task_outcome_task_id", "task_outcome", ["task_id"])
    op.create_index("ix_task_outcome_project_id", "task_outcome", ["project_id"])
    op.create_index("ix_task_outcome_agent_id", "task_outcome", ["agent_id"])


def downgrade() -> None:
    op.drop_table("task_outcome")
