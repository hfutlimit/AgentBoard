"""story kanban 标记: stories 表加 in_kanban 字段

Revision ID: z0a1b2c3d4e5
Revises: y7z8a9b0c1d2
Create Date: 2026-08-12

Epic 130 看板功能（2026-08-12）：
- in_kanban —— Story 是否进入项目看板（ticket 上「是否进入 kanban」标记）；
  标记后 worker 开始自动化处理。默认 0（不进看板）。
"""
from alembic import op
import sqlalchemy as sa

revision = "z0a1b2c3d4e5"
down_revision = "y7z8a9b0c1d2"


def upgrade() -> None:
    with op.batch_alter_table("stories") as batch:
        batch.add_column(sa.Column("in_kanban", sa.Boolean(), nullable=False,
                                   server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("stories") as batch:
        batch.drop_column("in_kanban")
