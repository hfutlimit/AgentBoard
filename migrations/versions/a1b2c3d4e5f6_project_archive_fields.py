"""project archive fields: is_archived / archived_at / archived_by

Revision ID: a1b2c3d4e5f6
Revises: h7i8j9k0l1m2
Create Date: 2026-08-15

Story 137（项目中心：项目归档机制）：
- 新增 projects.is_archived BOOLEAN NOT NULL DEFAULT 0（带索引，默认列表隐藏）
- 新增 projects.archived_at TIMESTAMP NULL（归档时间）
- 新增 projects.archived_by INT NULL → users.id（归档操作人，便于审计）

不影响已有数据：默认 0/None 即可保持原行为。降级直接 drop_column 三列。

注：down_revision 指向 h7i8j9k0l1m2（add AgentRun execution-time agent and model snapshot）
而不是 z8a9b0c1d2e3，以便和当前 h7i8j9k0l1m2 这一支 head 合并为单 head。
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "h7i8j9k0l1m2"


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column(
                "is_archived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column("archived_at", sa.DateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column("archived_by", sa.Integer(), nullable=True)
        )
        # 外键：archived_by → users.id
        batch.create_foreign_key(
            "fk_projects_archived_by_users",
            "users",
            ["archived_by"],
            ["id"],
        )
        # 索引：列表筛选 is_archived 频繁
        batch.create_index(
            "ix_projects_is_archived",
            ["is_archived"],
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("ix_projects_is_archived")
        batch.drop_constraint("fk_projects_archived_by_users", type_="foreignkey")
        batch.drop_column("archived_by")
        batch.drop_column("archived_at")
        batch.drop_column("is_archived")
