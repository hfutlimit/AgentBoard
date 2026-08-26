"""add agent_behavior_configs and learnings tables

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-08-26

? AgentBoard ???
1. agent_behavior_configs???????Agent ??WorkType ?????????
2. learnings????????????????
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. agent_behavior_configs
    op.create_table(
        "agent_behavior_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("work_type", sa.String(50), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("preset_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "agent_id", "work_type", name="uq_agent_behavior_config"),
    )
    op.create_index("ix_agent_behavior_configs_project_id", "agent_behavior_configs", ["project_id"])
    op.create_index("ix_agent_behavior_configs_agent_id", "agent_behavior_configs", ["agent_id"])
    op.create_index("ix_agent_behavior_configs_work_type", "agent_behavior_configs", ["work_type"])

    # 2. learnings
    op.create_table(
        "learnings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_type", sa.String(50), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_review_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_learnings_project_id", "learnings", ["project_id"])
    op.create_index("ix_learnings_agent_id", "learnings", ["agent_id"])
    op.create_index("ix_learnings_work_type", "learnings", ["work_type"])
    op.create_index("ix_learnings_category", "learnings", ["category"])
    op.create_index("ix_learnings_created_at", "learnings", ["created_at"])


def downgrade() -> None:
    op.drop_table("learnings")
    op.drop_table("agent_behavior_configs")
