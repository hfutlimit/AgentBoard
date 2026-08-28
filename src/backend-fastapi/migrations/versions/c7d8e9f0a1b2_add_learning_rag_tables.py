"""episode_embedding + project_playbook 表（Epic 140 切片 3）

Revision ID: c7d8e9f0a1b2
Revises: a2b3c4d5e6f7
Create Date: 2026-08-16

- episode_embedding：任务 run trace 向量化快照（episode_id=task_id 唯一，向量存 JSON TEXT）。
- project_playbook：项目级 Playbook markdown（按 project_id 唯一）。
"""
from alembic import op
import sqlalchemy as sa

revision = "c7d8e9f0a1b2"
down_revision = "a2b3c4d5e6f7"


def upgrade() -> None:
    op.create_table(
        "episode_embedding",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_type", sa.String(10), nullable=False, server_default="dev"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(10), nullable=False, server_default="success"),
        # MariaDB 11.7+ reserves VECTOR for its native vector type.  Keep the
        # established column name, but force identifier quoting so fresh
        # production databases can apply this historical migration.
        sa.Column("vector", sa.Text(), nullable=False, quote=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_episode_embedding_episode_id", "episode_embedding", ["episode_id"])
    op.create_index("ix_episode_embedding_project_id", "episode_embedding", ["project_id"])

    op.create_table(
        "project_playbook",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, unique=True),
        sa.Column("content_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_compressed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_project_playbook_project_id", "project_playbook", ["project_id"])


def downgrade() -> None:
    op.drop_table("project_playbook")
    op.drop_table("episode_embedding")
