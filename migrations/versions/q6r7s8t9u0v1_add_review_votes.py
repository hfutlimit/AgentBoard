"""Epic 122 切片 3 M3：多数决评审投票表

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0

S3 M3（评审强度升级：1 名 reviewer approve 即通过 → N 人多数决，文档 #50 §7 决策 #7）
增量迁移：

新建 ``review_votes`` 表 —— 一实体（Story/Task）多评审人投票记录：
- ``entity_type``：story | task；
- ``entity_id``：Story/Task 主键；
- ``reviewer_user_id``（FK users）：投票人；
- ``verdict``：approve | reject；
- ``comment_id``（FK comments，可空）：评审意见载体评论；
- ``round``：所属评审轮次（驳回后开新一轮，历史票随结算清空，MVP 简化）；
- UNIQUE(entity_type, entity_id, reviewer_user_id)：一人一票，改票 upsert。

双后端兼容（SQLite / MariaDB 均支持 create_table）；纯增量；
零新增依赖；不重建既有表、不破坏既有契约。
"""
from alembic import op
import sqlalchemy as sa

revision = "q6r7s8t9u0v1"
down_revision = "p5q6r7s8t9u0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "review_votes" in inspector.get_table_names():
        return
    op.create_table(
        "review_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=10), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column(
            "reviewer_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(length=10), nullable=False),
        sa.Column(
            "comment_id",
            sa.Integer(),
            sa.ForeignKey("comments.id"),
            nullable=True,
        ),
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "entity_type", "entity_id", "reviewer_user_id",
            name="uq_review_votes_entity_reviewer",
        ),
    )
    op.create_index(
        "ix_review_votes_entity", "review_votes", ["entity_type", "entity_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "review_votes" not in inspector.get_table_names():
        return
    op.drop_index("ix_review_votes_entity", table_name="review_votes")
    op.drop_table("review_votes")
