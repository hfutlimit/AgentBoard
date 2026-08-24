"""proposals.auto_retry_count —— Agent 不可用自动重投计数（2026-08-09）

Revision ID: w4x5y6z7a8b9
Revises: w3x4y5z6a7b8

后端 job（recover-failed）把「Agent 不可用」导致的 failed 提案自动回退 queued
重投。重投次数用独立字段计数（而非编码进 error 文本——worker 每次失败都会用
新错误覆盖 error，文本计数会丢失），达上限停投转人工。
双后端兼容：纯 ADD COLUMN。
"""
from alembic import op
import sqlalchemy as sa

revision = "w4x5y6z7a8b9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("proposals") as batch:
            batch.add_column(sa.Column("auto_retry_count", sa.Integer(),
                                       nullable=True, server_default="0"))
    else:
        op.add_column("proposals", sa.Column("auto_retry_count", sa.Integer(),
                                             nullable=True, server_default="0"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("proposals") as batch:
            batch.drop_column("auto_retry_count")
    else:
        op.drop_column("proposals", "auto_retry_count")
