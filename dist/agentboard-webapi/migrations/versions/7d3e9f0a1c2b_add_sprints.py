"""add sprints table and sprint_id on tasks

Revision ID: 7d3e9f0a1c2b
Revises: 8d6f0b7a2c41
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d3e9f0a1c2b"
down_revision: Union[str, None] = "8d6f0b7a2c41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    # Sprint 表
    op.create_table(
        "sprints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=True),
        sa.Column("end_date", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprints_project_id", "sprints", ["project_id"])

    # sprint_id on tasks（先加列，再建索引和约束）
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("sprint_id", sa.Integer(), nullable=True))
    op.create_index("ix_tasks_sprint_id", "tasks", ["sprint_id"])

    with op.batch_alter_table("sprints") as batch:
        batch.create_check_constraint(
            "ck_sprints_status",
            "status IN ('planning','active','completed')",
        )

    with op.batch_alter_table("tasks") as batch:
        batch.create_foreign_key(
            "fk_tasks_sprint_id_sprints", "tasks", ["sprint_id"], ["sprints.id"],
            ondelete="SET NULL",
        )

    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_sprint_id_sprints", type_="foreignkey")
    op.drop_index("ix_tasks_sprint_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("sprint_id")

    with op.batch_alter_table("sprints") as batch:
        batch.drop_constraint("ck_sprints_status", type_="check")

    op.drop_index("ix_sprints_project_id", table_name="sprints")
    op.drop_table("sprints")

    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")
