"""WorkerProjectMapping 一级实体（T6.3）

Revision ID: u9v0w1x2y3z4
Revises: t8u9v0w1x2y3
Create Date: 2026-09-02

``worker_project_mappings``：Worker ↔ Project 授权映射。
- 不含 user_id（T6.3 去冗余）：归属随 agent 走，映射只答「这台机器在不在
  项目工作面里」；
- 真实路径不上云：workspace/CLI 路径留在 worker 本地（local_registry）。
"""
from alembic import op
import sqlalchemy as sa

import logging


log = logging.getLogger("alembic.runtime.migration")

revision = "u9v0w1x2y3z4"
down_revision = "t8u9v0w1x2y3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_project_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_id", sa.String(length=64),
                  sa.ForeignKey("workers.worker_id"), nullable=False),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("worker_id", "project_id",
                            name="uq_worker_project_mappings_worker_project"),
    )
    op.create_index("ix_worker_project_mappings_worker_id",
                    "worker_project_mappings", ["worker_id"])
    op.create_index("ix_worker_project_mappings_project_id",
                    "worker_project_mappings", ["project_id"])
    log.info("worker_project_mappings 表创建完成")


def downgrade() -> None:
    op.drop_index("ix_worker_project_mappings_project_id",
                  "worker_project_mappings")
    op.drop_index("ix_worker_project_mappings_worker_id",
                  "worker_project_mappings")
    op.drop_table("worker_project_mappings")
