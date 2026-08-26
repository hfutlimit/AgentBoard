"""worker + agent_instance 二层模型（多 Worker 部署隔离）

Revision ID: a9b8c7d6e5f3
Revises: z8a9b0c1d2e3
Create Date: 2026-08-26

背景（2026-08-26 P1 修复）：
- ``Agent`` 表的 ``cli_command`` / ``online`` / ``probe_message`` 是全局字段，
  Worker A 探测失败会把全局 agent 置 offline，多 Worker 必然互殴。
- 把"逻辑 Agent 身份"和"Worker 上的 CLI 实例"解耦到 ``workers`` + ``agent_instances`` 两表。
- ``Agent.cli_command`` 字段保留不删（兼容旧单 Worker 部署的 ``/api/agents/{id}/probe`` 路径）。
- 旧 ``Agent.cli_command != ''`` 的数据迁移到 ``AgentInstance(worker_id="default")``。
"""
from alembic import op
import sqlalchemy as sa


revision = "a9b8c7d6e5f3"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- workers 表：机器身份 ----
    op.create_table(
        "workers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.String(64), nullable=False, unique=True),
        sa.Column("hostname", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_workers_worker_id", "workers", ["worker_id"], unique=True)
    op.create_index("ix_workers_status", "workers", ["status"])

    # ---- agent_instances 表：(worker_id, agent_id) 局部执行环境 ----
    op.create_table(
        "agent_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("cli_command", sa.String(500), nullable=False, server_default=""),
        sa.Column("model", sa.String(100), nullable=False, server_default=""),
        sa.Column("auth_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(), nullable=True),
        sa.Column("probe_message", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("worker_id", "agent_id", name="uq_agent_instance_worker_agent"),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["workers.worker_id"],
            name="fk_agent_instance_worker",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_instances_worker_id", "agent_instances", ["worker_id"])
    op.create_index("ix_agent_instances_agent_id", "agent_instances", ["agent_id"])
    op.create_index("ix_agent_instances_online", "agent_instances", ["online"])

    # ---- 数据迁移：插入 default worker（兼容旧单 Worker 部署） ----
    bind = op.get_bind()
    # 仅在 default worker 不存在时插入
    existing = bind.execute(
        sa.text("SELECT id FROM workers WHERE worker_id = :wid"),
        {"wid": "default"},
    ).first()
    if existing is None:
        op.execute(
            sa.text(
                "INSERT INTO workers (worker_id, hostname, status, created_at, updated_at) "
                "VALUES ('default', 'legacy-single-worker', 'active', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    # ---- 数据迁移：把旧 Agent.cli_command 落到 default instance ----
    # SQLite 的 server_default 在 INSERT ... SELECT 时对已存在列不生效，
    # 显式写列以保证数据完整。
    op.execute(
        sa.text(
            """
            INSERT INTO agent_instances
                (worker_id, agent_id, cli_command, model, auth_key, enabled, online,
                 last_heartbeat, last_probe_at, probe_message, created_at, updated_at)
            SELECT
                'default', agent_id, cli_command, model, auth_key, enabled, online,
                last_heartbeat, last_probe_at, probe_message, created_at, updated_at
            FROM agents
            WHERE cli_command != ''
              AND NOT EXISTS (
                SELECT 1 FROM agent_instances ai
                WHERE ai.worker_id = 'default' AND ai.agent_id = agents.agent_id
              )
            """
        )
    )


def downgrade() -> None:
    # 删 default instance（保留用户新挂的）
    op.execute("DELETE FROM agent_instances WHERE worker_id = 'default'")
    op.execute("DELETE FROM workers WHERE worker_id = 'default'")
    op.drop_index("ix_agent_instances_online", table_name="agent_instances")
    op.drop_index("ix_agent_instances_agent_id", table_name="agent_instances")
    op.drop_index("ix_agent_instances_worker_id", table_name="agent_instances")
    op.drop_table("agent_instances")
    op.drop_index("ix_workers_status", table_name="workers")
    op.drop_index("ix_workers_worker_id", table_name="workers")
    op.drop_table("workers")
