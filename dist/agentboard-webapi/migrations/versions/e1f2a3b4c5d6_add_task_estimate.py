"""add task estimate (Epic 32 Story 49.3)

Revision ID: e1f2a3b4c5d6
Revises: d6a1f4e8c2b7
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d6a1f4e8c2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 预估工时（小时），可为空。SQLite 原生 ADD COLUMN，无需 batch recreate。
    op.add_column("tasks", sa.Column("estimate", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "estimate")
