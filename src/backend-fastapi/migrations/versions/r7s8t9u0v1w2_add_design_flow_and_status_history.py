"""Epic 123：状态机扩展——设计评审流 + blocked 全向/历史

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1

增量迁移（存量零破坏）：

1. ``tasks`` 表：
   - 加 ``previous_status`` 列（进入 blocked 时记录上一状态，解除时恢复）；
   - ``ck_tasks_status`` CHECK 扩展 4 个设计评审态
     （in_design / design_pending_review / design_review_approved / final_review）。
2. ``stories`` 表：加 ``needs_design`` 列（Boolean NOT NULL，默认 true）。
3. 新建 ``task_status_history`` 表（每次状态变更追加一条，可审计）。

双后端兼容：SQLite 走 batch_alter_table 重建表改 CHECK；
MariaDB 走 drop_constraint + create_check_constraint。
"""
from alembic import op
import sqlalchemy as sa

revision = "r7s8t9u0v1w2"
down_revision = "q6r7s8t9u0v1"
branch_labels = None
depends_on = None

_NEW_STATUS_CHECK = (
    "status IN ('backlog','todo','in_progress','in_review','verifying','done',"
    "'blocked','in_design','design_pending_review','design_review_approved','final_review')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tcols = {c["name"] for c in inspector.get_columns("tasks")}
    if "previous_status" not in tcols:
        op.add_column(
            "tasks",
            sa.Column("previous_status", sa.String(length=20), nullable=True),
        )

    scols = {c["name"] for c in inspector.get_columns("stories")}
    if "needs_design" not in scols:
        op.add_column(
            "stories",
            sa.Column(
                "needs_design", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
        )

    if not inspector.has_table("task_status_history"):
        op.create_table(
            "task_status_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "task_id",
                sa.Integer(),
                sa.ForeignKey("tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("from_status", sa.String(length=20), nullable=False),
            sa.Column("to_status", sa.String(length=20), nullable=False),
            sa.Column(
                "changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
            ),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_task_status_history_task_id", "task_status_history", ["task_id"]
        )
        op.create_index(
            "ix_task_status_history_changed_by",
            "task_status_history",
            ["changed_by"],
        )

    # 更新 ck_tasks_status：SQLite 需 batch_alter_table（表重建），MariaDB drop+create
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_constraint("ck_tasks_status", type_="check")
            batch_op.create_check_constraint("ck_tasks_status", _NEW_STATUS_CHECK)
    else:
        op.drop_constraint("ck_tasks_status", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_status", "tasks", _NEW_STATUS_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_constraint("ck_tasks_status", type_="check")
            batch_op.create_check_constraint(
                "ck_tasks_status",
                "status IN ('backlog','todo','in_progress','in_review','verifying','done','blocked')",
            )
    else:
        op.drop_constraint("ck_tasks_status", "tasks", type_="check")
        op.create_check_constraint(
            "ck_tasks_status",
            "tasks",
            "status IN ('backlog','todo','in_progress','in_review','verifying','done','blocked')",
        )

    if inspector.has_table("task_status_history"):
        op.drop_index("ix_task_status_history_task_id", table_name="task_status_history")
        op.drop_index(
            "ix_task_status_history_changed_by", table_name="task_status_history"
        )
        op.drop_table("task_status_history")

    scols = {c["name"] for c in inspector.get_columns("stories")}
    if "needs_design" in scols:
        op.drop_column("stories", "needs_design")

    tcols = {c["name"] for c in inspector.get_columns("tasks")}
    if "previous_status" in tcols:
        op.drop_column("tasks", "previous_status")
