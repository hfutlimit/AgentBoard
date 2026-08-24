"""add project_members, notifications tables and is_private/is_admin fields

Revision ID: 1a2b3c4d5e6f
Revises: a5f2e8d9b0c1
Create Date: 2026-07-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, None] = "a5f2e8d9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    # 1. projects.is_private
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.text("0")))

    # 2. users.is_admin
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")))

    # 3. project_members（无需 CHECK，直接建表）
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_project_members_unique", "project_members", ["project_id", "user_id"], unique=True)

    # 4. notifications（无需 CHECK，直接建表）
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    op.drop_table("notifications")
    op.drop_index("ix_project_members_unique", table_name="project_members")
    op.drop_table("project_members")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_admin")

    with op.batch_alter_table("projects") as batch:
        batch.drop_column("is_private")

    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")
