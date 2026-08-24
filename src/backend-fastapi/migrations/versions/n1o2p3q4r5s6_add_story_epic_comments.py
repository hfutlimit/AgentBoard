"""comments 支持 Story / Epic 评论（Epic 97 扩展）

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5

Story / Epic 详情页需要与 Task 一致的评论能力：

- ``comments`` 新增 ``story_id``（FK stories）、``epic_id``（FK epics）可空列 + 索引。
- ``task_id`` 由 NOT NULL 改为可空（一条评论只属于 task / story / epic 三者其一）。

纯增量；既有 task 评论数据不受影响（task_id 保留原值）。
双后端兼容（SQLite / MariaDB）；零 REST 契约破坏；不触碰端口 18001。
"""
from alembic import op
import sqlalchemy as sa

revision = "n1o2p3q4r5s6"
down_revision = "m0n1o2p3q4r5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("comments")}
    if "story_id" not in columns:
        op.add_column("comments", sa.Column("story_id", sa.Integer(), nullable=True))
        op.create_index("ix_comments_story_id", "comments", ["story_id"])
    if "epic_id" not in columns:
        op.add_column("comments", sa.Column("epic_id", sa.Integer(), nullable=True))
        op.create_index("ix_comments_epic_id", "comments", ["epic_id"])
    # task_id 由 NOT NULL 改为可空（SQLite 不支持 ALTER COLUMN，需 batch 重建表）
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("comments") as batch_op:
            batch_op.alter_column("task_id", existing_type=sa.Integer(), nullable=True)
    else:
        op.alter_column("comments", "task_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("comments")}
    if "epic_id" in columns:
        op.drop_index("ix_comments_epic_id", table_name="comments")
        op.drop_column("comments", "epic_id")
    if "story_id" in columns:
        op.drop_index("ix_comments_story_id", table_name="comments")
        op.drop_column("comments", "story_id")
