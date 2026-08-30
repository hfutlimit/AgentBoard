"""Sprint 12 (Generic AgentWorker) — 多数决评审 fan-out

Epic 122 切片 3 M3 扩展：原 ``review_votes`` 表只存「已投票」记录（verdict
NOT NULL，approve | reject），多数决结算靠 ``_review_vote_counts`` 把所有
非空票加起来对比 quorum。

Sprint 12 的多数决 fan-out 需要：``assign_task_reviewer`` 一次挑 N 个
reviewer，每个 reviewer 一行 ``review_votes`` 记录，但尚未投票的 verdict
应为空——而不是先暂存别的表，等投票时再 insert。前者更省事（一张表管
两态：pending = NULL，cast = approve/reject），后者需要新表 + 关联。

本次迁移只动一处：
- ``review_votes.verdict`` 由 NOT NULL 改为 NULL（兼容双后端 SQLite / MariaDB）

无新增表、无破坏性变更。``_review_vote_counts`` 已用
``group_by(ReviewVote.verdict).all()`` 拿 dict，NULL 自然落进 counts 的
``None`` 键，下游 ``counts.get("approve", 0)`` / ``counts.get("reject", 0)``
正确返回 0；pending 行不计入 ``approve_n + reject_n``，法定票数 gate 仍
按"已投票数"判定，无需改动。
"""
from alembic import op
import sqlalchemy as sa

revision = "s9t0u1v2w3a4"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "review_votes" not in inspector.get_table_names():
        return  # 双后端首次迁移前可能尚未建表；上层 migration 已处理
    # SQLite 与 MariaDB 都支持直接 ALTER COLUMN；op.alter_column 在两条
    # backend 上都翻译成 "DROP NOT NULL"。批处理大小与现有 migration
    # 风格保持一致。
    with op.batch_alter_table("review_votes") as batch:
        batch.alter_column(
            "verdict",
            existing_type=sa.String(length=10),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "review_votes" not in inspector.get_table_names():
        return
    # 回滚前先清空 NULL 投票（无法映射回 approve/reject 的单值）
    op.execute("DELETE FROM review_votes WHERE verdict IS NULL")
    with op.batch_alter_table("review_votes") as batch:
        batch.alter_column(
            "verdict",
            existing_type=sa.String(length=10),
            nullable=False,
        )
