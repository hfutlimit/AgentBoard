"""task ownership: created_by_user_id + created_by_agent_id

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-09-01

归属收敛（仅本人 agent 可处理）：给 tasks 加 owner（created_by_user_id）与
创建方 agent（created_by_agent_id）。存量行保留 NULL，调度/认领端 fail closed，
需人工补 owner 后才能被处理。见 docs/design/agent-ownership-scoping-plan.md。
"""
from alembic import op
import sqlalchemy as sa


revision = "v3w4x5y6z7a8"
down_revision = "u2v3w4x5y6z7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("created_by_agent_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_created_by_user", "users", ["created_by_user_id"], ["id"],
        )
        batch.create_foreign_key(
            "fk_tasks_created_by_agent", "agents", ["created_by_agent_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tasks_created_by_user_id", ["created_by_user_id"])
        batch.create_index("ix_tasks_created_by_agent_id", ["created_by_agent_id"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_created_by_agent_id")
        batch.drop_index("ix_tasks_created_by_user_id")
        batch.drop_constraint("fk_tasks_created_by_agent", type_="foreignkey")
        batch.drop_constraint("fk_tasks_created_by_user", type_="foreignkey")
        batch.drop_column("created_by_agent_id")
        batch.drop_column("created_by_user_id")
