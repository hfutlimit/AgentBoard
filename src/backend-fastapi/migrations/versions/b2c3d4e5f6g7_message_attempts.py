"""message_attempts 表（Phase 2 P1：持久化 MQ retry 计数）

Revision ID: b2c3d4e5f6g7
Revises: a9b8c7d6e5f3
Create Date: 2026-08-26

背景（2026-08-26 P1 review 指出）：
- 原 ``ProcessorCoordinator._msg_retries`` 是进程内 dict，多 Worker / Worker restart
  / RabbitMQ requeue 时全部失效 → 单条消息可被重试任意次（极端无限重试）。
- 修复 = 把 attempt 计数持久化到 DB。表结构：``message_attempts``，
  UNIQUE(execution_id) 保证 upsert 幂等；status 状态机
  pending → in_flight → (completed | dead_lettered | gave_up)；
  next_retry_at 索引加速"现在能不能重试"判断。
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6g7"
down_revision = "a9b8c7d6e5f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.String(256), nullable=False, unique=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(1000), nullable=False, server_default=""),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_event", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_entity_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("last_entity_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_ref_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "status IN ('pending','in_flight','completed','dead_lettered','gave_up')",
            name="ck_message_attempts_status",
        ),
    )
    op.create_index("ix_message_attempts_execution_id", "message_attempts",
                    ["execution_id"], unique=True)
    op.create_index("ix_message_attempts_next_retry_at", "message_attempts",
                    ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_message_attempts_next_retry_at", table_name="message_attempts")
    op.drop_index("ix_message_attempts_execution_id", table_name="message_attempts")
    op.drop_table("message_attempts")
