"""tasks.needs_human_confirmation: design task 等用户确认 gate

Revision ID: t1u2v3w4x5y6
Revises: s9t0u1v2w3a4
Create Date: 2026-08-31

PR-6 (P0-5 修复)：设计任务（type='design'）完成后不应直接进
agent review → user 没有机会确认设计就被开发了。

本迁移加 ``tasks.needs_human_confirmation`` BOOLEAN NOT NULL DEFAULT FALSE：

- 已有行默认 False（保持 legacy 行为：design task 走 agent auto-review）
- 新 design task 创建时 service 显式置 True
- task 进入 in_review 时若 flag=True → 跳过自动指派 reviewer，
  走 user_confirm 端点（POST /api/tasks/{id}/user_confirm）
- user_confirm 后正常 done → 触发 dependency unlock

新增端点：
- POST /api/tasks/{tid}/user_confirm  → 状态 done
- POST /api/tasks/{tid}/user_reject   → 状态回退 in_progress

不引入新 status 值（在 5 值状态机内：task 保持 in_review 状态，
用 flag 区分"等 reviewer" vs "等用户"）。UI 层按 flag 过滤 / 区分提示。
"""
from alembic import op
import sqlalchemy as sa


revision = "t1u2v3w4x5y6"
down_revision = "s9t0u1v2w3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite ALTER TABLE ADD COLUMN 直接走 op.add_column；MariaDB 同
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column(
                "needs_human_confirmation",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    # 索引：按 (project_id, status, needs_human_confirmation) 拉
    # 等用户确认的 design task（user_confirm inbox 用）
    op.create_index(
        "ix_tasks_needs_human_confirmation",
        "tasks",
        ["needs_human_confirmation", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_needs_human_confirmation", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("needs_human_confirmation")
