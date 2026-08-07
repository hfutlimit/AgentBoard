"""Epic 122 切片 2 M2：Task 评审闭环字段

Revision ID: p5q6r7s8t9u0
Revises: o5p6q7r8s9t0

S2 M2（开发任务提交评审后 → Task reviewer 指派 + approve/reject）增量迁移：

``tasks`` 表加评审闭环列（与 ``stories`` 的 S1 评审列对齐）：
- ``reviewer_id``（FK users，可空，index）：被指派评审人（随机分配器 CAS 回填）；
- ``review_round``（int default 0）：评审轮次计数（护栏，上限 5 → blocked）。

双后端兼容（SQLite add_column / MariaDB add_column 均直接支持）；
纯增量，不重建既有表、不破坏既有契约；零新增依赖。
"""
from alembic import op
import sqlalchemy as sa

revision = "p5q6r7s8t9u0"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("tasks")}
    if "reviewer_id" not in cols:
        op.add_column("tasks", sa.Column("reviewer_id", sa.Integer(), nullable=True))
        op.create_index("ix_tasks_reviewer_id", "tasks", ["reviewer_id"])
    if "review_round" not in cols:
        op.add_column(
            "tasks",
            sa.Column("review_round", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("tasks")}
    if "reviewer_id" in cols:
        op.drop_index("ix_tasks_reviewer_id", table_name="tasks")
        op.drop_column("tasks", "reviewer_id")
    if "review_round" in cols:
        op.drop_column("tasks", "review_round")
