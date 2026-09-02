"""per-agent review voting: review_votes 唯一键改到 reviewer_agent_id（NOT NULL）

Revision ID: p4q5r6s7t8u9
Revises: x7y8z9a0b1c2
Create Date: 2026-09-02

评审计票单位从「用户」改为「Agent」（Implementation Plan T1.1）：

- 唯一约束 ``uq_review_votes_entity_reviewer``（entity + reviewer_user_id）
  → ``uq_review_votes_entity_reviewer_agent``（entity + reviewer_agent_id）；
- ``reviewer_agent_id`` 改 **NOT NULL**：含 NULL 列的 UNIQUE 在 SQLite /
  MariaDB 下都允许重复（NULL != NULL），留着等于绕过一 agent 一票；
- FK ondelete ``SET NULL`` → ``CASCADE``：agent 注销后残留的票没有意义，
  且 NOT NULL 列上的 SET NULL 会让删 agent 直接失败。

数据迁移策略（不可判定时宁可丢占位票，也不猜投票人）：
  1. 先把 NULL agent 回填成该 user 名下 id 最小的 agent（保住已投出的票，
     它参与 quorum 计票，删掉会改变结算结果）；
  2. 仍为 NULL（该 user 一个 agent 都没有）→ **只删 verdict IS NULL 的
     占位票**；已投出的票无处可归但必须保住，此时无法加 NOT NULL，
     迁移会报错要求人工介入（见下方 fail-fast 检查）。
"""
from alembic import op
import sqlalchemy as sa


revision = "p4q5r6s7t8u9"
down_revision = "x7y8z9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ---- 1) 回填：NULL agent ← 该 user 名下 id 最小的 agent ----
    conn.execute(sa.text(
        "UPDATE review_votes SET reviewer_agent_id = ("
        "  SELECT a.id FROM agents a"
        "   WHERE a.user_id = review_votes.reviewer_user_id"
        "   ORDER BY a.id ASC LIMIT 1"
        ") WHERE reviewer_agent_id IS NULL"
    ))
    # ---- 2) 删掉无主占位票（verdict IS NULL 的指派占位行）----
    conn.execute(sa.text(
        "DELETE FROM review_votes"
        " WHERE reviewer_agent_id IS NULL AND verdict IS NULL"
    ))
    # ---- 3) fail-fast：还有已投出的票无法归属 → 停在迁移，人工处理 ----
    orphan = conn.execute(sa.text(
        "SELECT COUNT(*) FROM review_votes WHERE reviewer_agent_id IS NULL"
    )).scalar()
    if orphan:
        raise RuntimeError(
            f"review_votes: {orphan} 行已投出的票无法归属到任何 agent"
            "（对应用户没有 agent 记录）。请先为这些用户注册 agent，"
            "或手工清理这些票行，再重跑迁移。"
        )

    # ---- 4) DDL：唯一键 / NOT NULL / FK CASCADE ----
    with op.batch_alter_table("review_votes", schema=None) as batch:
        batch.drop_constraint("uq_review_votes_entity_reviewer", type_="unique")
        batch.alter_column(
            "reviewer_agent_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_review_votes_entity_reviewer_agent",
            ["entity_type", "entity_id", "reviewer_agent_id"],
        )
        # SQLite 不持久化 FK 名，反射回来的是 SQLAlchemy 默认命名；
        # 原迁移用的自定义名在 SQLite 上已经不存在，两个都试一遍。
        for fk_name in ("fk_review_votes_reviewer_agent",
                        "fk_review_votes_reviewer_agent_id_agents"):
            try:
                batch.drop_constraint(fk_name, type_="foreignkey")
                break
            except Exception:  # noqa: BLE001 — 名字不存在即下一个
                continue
        batch.create_foreign_key(
            "fk_review_votes_reviewer_agent", "agents",
            ["reviewer_agent_id"], ["id"], ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("review_votes", schema=None) as batch:
        batch.drop_constraint(
            "uq_review_votes_entity_reviewer_agent", type_="unique")
        for fk_name in ("fk_review_votes_reviewer_agent",
                        "fk_review_votes_reviewer_agent_id_agents"):
            try:
                batch.drop_constraint(fk_name, type_="foreignkey")
                break
            except Exception:  # noqa: BLE001
                continue
        batch.create_foreign_key(
            "fk_review_votes_reviewer_agent", "agents",
            ["reviewer_agent_id"], ["id"], ondelete="SET NULL",
        )
        batch.alter_column(
            "reviewer_agent_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
        batch.create_unique_constraint(
            "uq_review_votes_entity_reviewer",
            ["entity_type", "entity_id", "reviewer_user_id"],
        )
