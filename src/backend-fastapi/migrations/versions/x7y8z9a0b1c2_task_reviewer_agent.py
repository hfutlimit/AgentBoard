"""reviewer-by-agent: tasks.reviewer_agent_id + review_votes.reviewer_agent_id

Revision ID: x7y8z9a0b1c2
Revises: v3w4x5y6z7a8
Create Date: 2026-09-02

归属收敛（评审侧）：评审人按 Agent 维度记录（owner 名下、非实现方 agent），
reviewer_id(users.id) 保留做一人一票与鉴权，agent 列用于路由与审计。
"""
from alembic import op
import sqlalchemy as sa


revision = "x7y8z9a0b1c2"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("reviewer_agent_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_reviewer_agent", "agents", ["reviewer_agent_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tasks_reviewer_agent_id", ["reviewer_agent_id"])

    with op.batch_alter_table("review_votes") as batch:
        batch.add_column(sa.Column("reviewer_agent_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_review_votes_reviewer_agent", "agents", ["reviewer_agent_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("review_votes") as batch:
        batch.drop_constraint("fk_review_votes_reviewer_agent", type_="foreignkey")
        batch.drop_column("reviewer_agent_id")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_reviewer_agent_id")
        batch.drop_constraint("fk_tasks_reviewer_agent", type_="foreignkey")
        batch.drop_column("reviewer_agent_id")
