"""add task priority and comments

Revision ID: b39f1d2c7a10
Revises: 05e694deb9d6
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b39f1d2c7a10"
down_revision: Union[str, None] = "05e694deb9d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("priority", sa.String(length=10),
                                     nullable=False, server_default="medium"))
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comments_task_id", "comments", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_comments_task_id", table_name="comments")
    op.drop_table("comments")
    op.drop_column("tasks", "priority")
