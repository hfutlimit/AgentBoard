"""Epic 22: audit_logs, task_dependencies, webhook_configs

Revision ID: 9f8c2e7d1a4c
Revises: c4e8a1b2d3f4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f8c2e7d1a4c"
down_revision: Union[str, None] = "9f8c2e7d1a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # task_dependencies
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_id", sa.Integer(), nullable=False),
        sa.Column("dependency_type", sa.String(length=20), nullable=False, server_default="blocks"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_dependencies_task_id", "task_dependencies", ["task_id"])
    op.create_index("ix_task_dependencies_depends_on_id", "task_dependencies", ["depends_on_id"])

    # webhook_configs
    op.create_table(
        "webhook_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("secret", sa.String(length=256), nullable=True),
        sa.Column("events", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_configs_project_id", "webhook_configs", ["project_id"])
    op.create_index("ix_webhook_configs_enabled", "webhook_configs", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_webhook_configs_enabled", table_name="webhook_configs")
    op.drop_index("ix_webhook_configs_project_id", table_name="webhook_configs")
    op.drop_table("webhook_configs")

    op.drop_index("ix_task_dependencies_depends_on_id", table_name="task_dependencies")
    op.drop_index("ix_task_dependencies_task_id", table_name="task_dependencies")
    op.drop_table("task_dependencies")

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")
