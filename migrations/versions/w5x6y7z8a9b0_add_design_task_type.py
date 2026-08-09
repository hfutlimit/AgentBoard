"""tasks 表新增 design 任务类型（2026-08-09 每个 Story 自动带设计任务）

Revision ID: w5x6y7z8a9b0
Revises: w4x5y6z7a8b9

ck_tasks_type 从 3 值扩为 4 值（task / bug / test_execution / design）。

双后端兼容：SQLite 用 batch_alter_table（重建表），MariaDB 直接 alter。
"""
from alembic import op

revision = "w5x6y7z8a9b0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None

_TYPE_CHECK = "type IN ('task','bug','test_execution','design')"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("tasks") as batch:
            batch.drop_constraint("ck_tasks_type", type_="check")
            batch.create_check_constraint("ck_tasks_type", _TYPE_CHECK)
    else:
        op.drop_constraint("ck_tasks_type", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_type", "tasks", _TYPE_CHECK)


def downgrade() -> None:
    old = "type IN ('task','bug','test_execution')"
    if _is_sqlite():
        with op.batch_alter_table("tasks") as batch:
            batch.drop_constraint("ck_tasks_type", type_="check")
            batch.create_check_constraint("ck_tasks_type", old)
    else:
        op.drop_constraint("ck_tasks_type", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_type", "tasks", old)
