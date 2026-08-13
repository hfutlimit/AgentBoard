"""task status simplify: 5 状态集 + status_reason + 4 类型

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
Create Date: 2026-08-13

Story 265 收敛（任务状态精简）：
- Status: 11 值 → 5 值（todo/in_progress/in_review/done/blocked）
  * backlog           → todo
  * in_design         → in_progress
  * design_pending_review → in_progress
  * design_review_approved → in_progress
  * final_review      → done
  * verifying         → in_progress
- ItemType: 4 值收敛
  * task         → dev
  * test_execution → qa
  * design/bug   保留
- 新增 status_reason 列（String(40) 可空，done/blocked 必填合法值，其他为空）
- 删除 task_status_history 全表（设计评审段消失，重新开始记录）
- 收紧 CheckConstraint
"""
from alembic import op
import sqlalchemy as sa

revision = "z8a9b0c1d2e3"
# Story 265：down_revision 改为 z0a1b2c3d4e5（story kanban marker 迁移），
# 因为远端 main 有 z0a1b2c3d4e5 从 y7z8a9b0c1d2 分叉出去，避免 dual head。
down_revision = "z0a1b2c3d4e5"


# 状态迁移表（status）
_STATUS_MAP = {
    "backlog": "todo",
    "in_design": "in_progress",
    "design_pending_review": "in_progress",
    "design_review_approved": "in_progress",
    "verifying": "in_progress",
    "final_review": "done",
    # 已经是 5 值的不动：todo/in_progress/in_review/done/blocked
}
# 类型迁移表（type）
_TYPE_MAP = {
    "task": "dev",
    "test_execution": "qa",
    # 保留：design/bug
}


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 先放宽 CheckConstraint + 加 status_reason 列（顺序：放宽 → 加列 → 数据迁移 → 收紧）
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("ck_tasks_type", type_="check")
        batch.drop_constraint("ck_tasks_status", type_="check")
        # 提前加 status_reason 列（nullable），便于后续 backfill
        batch.add_column(sa.Column("status_reason", sa.String(40), nullable=True))

    # 2) 数据迁移：status / type 映射
    for old_status, new_status in _STATUS_MAP.items():
        bind.execute(
            sa.text("UPDATE tasks SET status = :new WHERE status = :old"),
            {"new": new_status, "old": old_status},
        )
    for old_type, new_type in _TYPE_MAP.items():
        bind.execute(
            sa.text("UPDATE tasks SET type = :new WHERE type = :old"),
            {"new": new_type, "old": old_type},
        )

    # 3) 清空 task_status_history（设计评审段消失，状态机变化导致历史不可读）
    bind.execute(sa.text("DELETE FROM task_status_history"))

    # 3.5) backfill status_reason：保证迁移后没有 status='done'/'blocked' 但 reason IS NULL
    #      的脏数据，避免「DB 物理层允许 NULL、业务层宣称必填」的两套语义。
    #      - done: 默认 completed（向前兼容：新数据主流选择）
    #      - blocked: legacy（迁移专用 reason，UI 可后续让用户重选真实原因）
    bind.execute(
        sa.text("UPDATE tasks SET status_reason = 'completed' "
                "WHERE status = 'done' AND status_reason IS NULL")
    )
    bind.execute(
        sa.text("UPDATE tasks SET status_reason = 'legacy' "
                "WHERE status = 'blocked' AND status_reason IS NULL")
    )

    # 4) 收紧 CheckConstraint
    with op.batch_alter_table("tasks") as batch:
        batch.create_check_constraint(
            "ck_tasks_type", "type IN ('dev','bug','qa','design')",
        )
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('todo','in_progress','in_review','done','blocked')",
        )

    # 5) 迁移一致性校验：旧状态/类型应全为 0
    for old_status in _STATUS_MAP:
        count = bind.execute(
            sa.text("SELECT COUNT(*) FROM tasks WHERE status = :s"),
            {"s": old_status},
        ).scalar()
        if count:
            raise RuntimeError(
                f"data migration incomplete: {count} tasks still have status='{old_status}'"
            )
    for old_type in _TYPE_MAP:
        count = bind.execute(
            sa.text("SELECT COUNT(*) FROM tasks WHERE type = :t"),
            {"t": old_type},
        ).scalar()
        if count:
            raise RuntimeError(
                f"data migration incomplete: {count} tasks still have type='{old_type}'"
            )
    # Story 265 增补：done/blocked 数据必须有 status_reason（业务层不变量）
    for st in ("done", "blocked"):
        null_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM tasks WHERE status = :s AND status_reason IS NULL"),
            {"s": st},
        ).scalar()
        if null_count:
            raise RuntimeError(
                f"data migration incomplete: {null_count} tasks have status='{st}' "
                f"but status_reason IS NULL (backfill failed)"
            )


def downgrade() -> None:
    bind = op.get_bind()

    # 1) 放宽 status_reason 列（无逆操作；保留数据）
    # 2) 还原 status / type 映射（粗略：5 值 → 旧值；会丢失细粒度信息）
    _REVERSE_STATUS = {
        # 旧值优先用 in_progress（兜底），从 in_progress 不可能精确还原
        # 此处不主动改 status（保留新值），但放宽 CheckConstraint
        # 真实回滚应从备份恢复
    }

    # 3) 还原 CheckConstraint
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.drop_constraint("ck_tasks_type", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('backlog','todo','in_progress','in_review','verifying',"
            "'done','blocked','in_design','design_pending_review',"
            "'design_review_approved','final_review')",
        )
        batch.create_check_constraint(
            "ck_tasks_type", "type IN ('task','bug','test_execution','design')",
        )
        # status_reason 保留
