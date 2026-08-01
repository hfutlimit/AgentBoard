"""add proposals.claimed_by / claimed_at (Epic 96 P2-0：CAS 原子认领与显式租约)

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9

为 ``proposals`` 增加两列承载「认领租约」：

- ``claimed_by``：当前持有 analyzing 租约的 Worker 服务账号，空串表示无人持有。
- ``claimed_at``：租约起算时刻，**只在认领成功时写入**。

为什么不能复用 ``updated_at``：该列带 ``onupdate``，任何无关写入（用户作答、
PATCH converged_spec、补写轮次）都会刷新它。若以它判定租约是否过期，一个已崩溃
Worker 持有的提案会被旁人的写操作不断续期，永久卡死在 analyzing —— 崩溃恢复这条
唯一的丢单兜底就此失效。故租约必须挂在独立、只由认领动作推进的字段上。

两列均可空（``claimed_by`` 另给 server_default ''），存量行无需回填即可安全升级。
双后端兼容（SQLite / MariaDB）：仅用 add_column，不触发 SQLite 表重建。
"""
from alembic import op
import sqlalchemy as sa

revision = "i5j6k7l8m9n0"
down_revision = "h4i5j6k7l8m9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("proposals")}
    if "claimed_by" not in columns:
        op.add_column(
            "proposals",
            sa.Column("claimed_by", sa.String(length=100), nullable=True, server_default=""),
        )
    if "claimed_at" not in columns:
        op.add_column(
            "proposals",
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
        )
    # 租约回收扫描按 (status, claimed_at) 过滤，建复合索引避免全表扫描。
    indexes = {index["name"] for index in inspector.get_indexes("proposals")}
    if "ix_proposals_status_claimed_at" not in indexes:
        op.create_index(
            "ix_proposals_status_claimed_at", "proposals", ["status", "claimed_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_proposals_status_claimed_at", table_name="proposals")
    op.drop_column("proposals", "claimed_at")
    op.drop_column("proposals", "claimed_by")
