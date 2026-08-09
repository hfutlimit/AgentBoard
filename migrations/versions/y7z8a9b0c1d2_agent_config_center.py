"""agent config center: agents 表加 model/probe 字段

Revision ID: y7z8a9b0c1d2
Revises: x6y7z8a9b0c1
Create Date: 2026-08-09

Agent 配置中心化（2026-08-09）：
- model        —— cli_command 模板 {model} 占位符注入的模型名（同 CLI 多 agent 不同模型）
- probe_message —— Worker 定期 probe 结果详情（前端实时展示）
- last_probe_at —— 上次 probe 时间
- enabled      —— 停用则 worker 跳过 probe 与拉起（保留注册记录）
"""
from alembic import op
import sqlalchemy as sa

revision = "y7z8a9b0c1d2"
down_revision = "x6y7z8a9b0c1"


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("model", sa.String(100), nullable=False,
                                   server_default=""))
        batch.add_column(sa.Column("probe_message", sa.String(300), nullable=False,
                                   server_default=""))
        batch.add_column(sa.Column("last_probe_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False,
                                   server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("model")
        batch.drop_column("probe_message")
        batch.drop_column("last_probe_at")
        batch.drop_column("enabled")
