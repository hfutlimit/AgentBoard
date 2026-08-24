"""Epic 123：状态列宽 20→40（design_pending_review 等 21+ 字符超 VARCHAR(20)）

Revision ID: r8s9t0u1v2w3
Revises: r7s8t9u0v1w2

MariaDB 严格校验列宽，`design_pending_review`(21)/`design_review_approved`(23) 会
触发 Data too long (1406)。扩宽 tasks/stories/epics.status、tasks.previous_status、
task_status_history.from_status/to_status。
SQLite 不强制长度，batch_alter_table 兼容。
"""
from alembic import op
import sqlalchemy as sa

revision = "r8s9t0u1v2w3"
down_revision = "r7s8t9u0v1w2"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("tasks", "status", False),
    ("tasks", "previous_status", True),
    ("stories", "status", False),
    ("epics", "status", False),
    ("task_status_history", "from_status", False),
    ("task_status_history", "to_status", False),
]


def upgrade() -> None:
    bind = op.get_bind()
    for table, col, nullable in _COLUMNS:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    col, existing_type=sa.String(length=20), type_=sa.String(length=40),
                    existing_nullable=nullable, nullable=nullable,
                )
        else:
            op.alter_column(
                table, col, existing_type=sa.String(length=20), type_=sa.String(length=40),
                existing_nullable=nullable, nullable=nullable,
            )


def downgrade() -> None:
    bind = op.get_bind()
    for table, col, nullable in _COLUMNS:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(
                    col, existing_type=sa.String(length=40), type_=sa.String(length=20),
                    existing_nullable=nullable, nullable=nullable,
                )
        else:
            op.alter_column(
                table, col, existing_type=sa.String(length=40), type_=sa.String(length=20),
                existing_nullable=nullable, nullable=nullable,
            )
