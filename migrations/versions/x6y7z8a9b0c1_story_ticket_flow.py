"""Ticket 全流程 —— Story 状态机工单化（2026-08-09）

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0

1. ck_stories_status 缩为 8 值（backlog/confirmed/todo/in_progress/in_review/
   verifying/done/blocked）：移除 pending_review/ready，新增 confirmed（用户确认闸门）；
2. 存量数据映射：pending_review → todo（评审职责已下沉 Task 层）、
   ready → confirmed（评审通过等价于用户确认可执行）；
3. 新建 story_status_history 表（与 task_status_history 同构）。

双后端兼容：SQLite 改约束用 batch_alter_table（重建表），MariaDB 直接 alter。
注意顺序：先 UPDATE 存量数据，再重建 CHECK 约束（否则新约束拒绝旧值）。
"""
from alembic import op
import sqlalchemy as sa

revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None

_NEW_STATUS_CHECK = (
    "status IN ('backlog','confirmed','todo','in_progress','in_review',"
    "'verifying','done','blocked')"
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    # 1) 存量数据映射（必须在重建约束之前执行；目标值必须落在旧 9 值 CHECK 内）
    #    - pending_review（待评审）→ todo：Story 评审职责已下沉 Task 层，回到待办池；
    #    - ready（评审通过）→ todo：就绪可开发等价于待办（confirmed 是用户确认闸门，
    #      存量 ready 系评审产出而非用户确认，不宜直接映射 confirmed——且 confirmed
    #      不在旧 CHECK 内，直接 UPDATE 会被约束拒绝，故统一落 todo）。
    op.execute("UPDATE stories SET status='todo' WHERE status IN ('pending_review','ready')")

    # 2) 重建 ck_stories_status（8 值）
    if _is_sqlite():
        with op.batch_alter_table("stories") as batch:
            batch.drop_constraint("ck_stories_status", type_="check")
            batch.create_check_constraint("ck_stories_status", _NEW_STATUS_CHECK)
    else:
        op.drop_constraint("ck_stories_status", "stories", type_="check")
        op.create_check_constraint("ck_stories_status", "stories", _NEW_STATUS_CHECK)

    # 3) 新建 story_status_history 表
    op.create_table(
        "story_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=False),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_story_status_history_story_id", "story_status_history", ["story_id"])
    op.create_index("ix_story_status_history_changed_by", "story_status_history", ["changed_by"])


def downgrade() -> None:
    op.drop_index("ix_story_status_history_changed_by", table_name="story_status_history")
    op.drop_index("ix_story_status_history_story_id", table_name="story_status_history")
    op.drop_table("story_status_history")

    # 逆向数据映射：confirmed → ready（评审就绪语义近似）、其余恢复 9 值约束
    op.execute("UPDATE stories SET status='ready' WHERE status='confirmed'")

    _OLD_STATUS_CHECK = (
        "status IN ('backlog','todo','in_progress','in_review','verifying','done',"
        "'pending_review','ready','blocked')"
    )
    if _is_sqlite():
        with op.batch_alter_table("stories") as batch:
            batch.drop_constraint("ck_stories_status", type_="check")
            batch.create_check_constraint("ck_stories_status", _OLD_STATUS_CHECK)
    else:
        op.drop_constraint("ck_stories_status", "stories", type_="check")
        op.create_check_constraint("ck_stories_status", "stories", _OLD_STATUS_CHECK)
