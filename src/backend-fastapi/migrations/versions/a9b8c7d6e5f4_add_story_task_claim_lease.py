"""add stories/tasks claim lease columns (worker 崩溃回收兜底)

Revision ID: a9b8c7d6e5f4
Revises: 7h8i9j0k1l2m

为 ``stories`` / ``tasks`` 各增加两列承载「认领租约」，语义与 proposals
（迁移 i5j6k7l8m9n0）对齐：

- ``claimed_by``：当前持有认领的 Worker 身份串，空串表示非 worker 持有。
- ``claimed_at``：租约起算时刻，**只在认领成功时写入**。

背景：此前 ``claim_story``（confirmed→todo）与 ``claim_development_task``
（todo→in_progress）都是无归属的裸 CAS —— Worker 进程被 kill 后，Story
永久卡 todo、Task 永久卡 in_progress，没有任何自动恢复路径（proposals 有
reclaim-stale，这两类没有）。本迁移补齐数据基础，配套端点：

- POST /api/stories/reclaim-stale   （todo + 租约过期 → confirmed）
- POST /api/tasks/reclaim-stale     （in_progress + 租约过期 → todo）

安全边界：
- 只回收 claimed_by 非空的行 —— 用户手工置 todo / 认领的行不受影响；
- Task 的 in_progress 是人机共享状态，额外要求 updated_at < cutoff，
  认领后有任何后续写入（如评审驳回回退）一律不回收；
- Story 不做 updated_at 兜底（todo 可由用户手工创建，语义与 proposals
  的 analyzing「worker 专属」不同，误收风险大于漏收）。

双后端兼容（SQLite / MariaDB）：仅 add_column / create_index。
"""
from alembic import op
import sqlalchemy as sa

revision = "a9b8c7d6e5f4"
down_revision = "7h8i9j0k1l2m"
branch_labels = None
depends_on = None


def _add_lease_columns(table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(table)}
    if "claimed_by" not in columns:
        op.add_column(
            table,
            sa.Column("claimed_by", sa.String(length=100), nullable=True,
                      server_default=""),
        )
    if "claimed_at" not in columns:
        op.add_column(
            table,
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
        )
    indexes = {index["name"] for index in inspector.get_indexes(table)}
    ix = f"ix_{table}_status_claimed_at"
    if ix not in indexes:
        op.create_index(ix, table, ["status", "claimed_at"])


def upgrade() -> None:
    _add_lease_columns("stories")
    _add_lease_columns("tasks")


def downgrade() -> None:
    op.drop_index("ix_tasks_status_claimed_at", table_name="tasks")
    op.drop_column("tasks", "claimed_at")
    op.drop_column("tasks", "claimed_by")
    op.drop_index("ix_stories_status_claimed_at", table_name="stories")
    op.drop_column("stories", "claimed_at")
    op.drop_column("stories", "claimed_by")
