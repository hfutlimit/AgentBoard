"""owner_transferred 通知类型 + owner_transfer_history 表（T5.1/T5.2）

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-09-02

T5.1：notifications.type 的 DB CHECK 加 ``owner_transferred``。
T5.2：新建 ``owner_transfer_history`` 表。

三重白名单（缺一不可，只改一处必被另一层拦截）：
  1. 本迁移改的 DB CHECK（identity/models.py 同步声明）；
  2. features/notifications/service.py 的 ``valid_types`` 集合；
  3. features/notifications/schemas.py 的 pydantic pattern。
本次三处 + 服务层集合同批改。

生产 MariaDB：改 CHECK 走 ``batch_alter_table``（参照 v3w4x5y6z7a8 先例）；
owner_transfer_history 行数与移交频次同阶，很小，锁表可忽略。
"""
from alembic import op
import sqlalchemy as sa

import logging


log = logging.getLogger("alembic.runtime.migration")

revision = "t8u9v0w1x2y3"
down_revision = "s7t8u9v0w1x2"
branch_labels = None
depends_on = None

OLD_CHECK = (
    "type IN ('project_invite','join_request','task_assigned','status_changed','mentioned')"
)
NEW_CHECK = (
    "type IN ('project_invite','join_request','task_assigned','status_changed',"
    "'mentioned','owner_transferred')"
)


def _has_notifications_check(conn) -> bool:
    """notifications 表上是否真有 ck_notifications_type。

    实测：原始建表迁移（1a2b3c4d5e6f）的注释写着「notifications（无需 CHECK，
    直接建表）」—— CHECK 只存在于 ORM 声明，迁移建出的库**没有**这个约束。
    这里按方言探测，存在才改写，不存在直接跳过（batch_alter_table 对
    不存在的约束会抛 "No such constraint"）。
    """
    if conn.dialect.name == "sqlite":
        ddl = conn.execute(sa.text(
            "SELECT sql FROM sqlite_master"
            " WHERE type='table' AND name='notifications'"
        )).scalar() or ""
        return "ck_notifications_type" in ddl
    row = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.check_constraints"
        " WHERE constraint_schema = DATABASE()"
        "   AND constraint_name = 'ck_notifications_type' LIMIT 1"
    )).scalar()
    return row is not None


def upgrade() -> None:
    # ---- T5.2：owner_transfer_history ----
    op.create_table(
        "owner_transfer_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("from_owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("to_owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_owner_transfer_history_entity_id",
                    "owner_transfer_history", ["entity_id"])
    op.create_index("ix_owner_transfer_history_project_id",
                    "owner_transfer_history", ["project_id"])
    log.info("owner_transfer_history 表创建完成")

    # ---- T5.1：notifications.type CHECK 扩容（条件执行）----
    conn = op.get_bind()
    if _has_notifications_check(conn):
        with op.batch_alter_table("notifications") as batch_op:
            batch_op.drop_constraint("ck_notifications_type", type_="check")
        with op.batch_alter_table("notifications") as batch_op:
            batch_op.create_check_constraint("ck_notifications_type", NEW_CHECK)
        log.info("notifications.type CHECK 已扩容（+owner_transferred）")
    else:
        log.info(
            "notifications 表没有 ck_notifications_type 约束（原始建表未带"
            " CHECK），类型白名单由 ORM 声明 + service/pydantic 校验兜底，跳过"
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_notifications_check(conn):
        n = conn.execute(sa.text(
            "SELECT COUNT(*) FROM notifications WHERE type = 'owner_transferred'"
        )).scalar() or 0
        if n:
            raise RuntimeError(
                f"downgrade: 存在 {n} 条 owner_transferred 通知，缩 CHECK 会失败；"
                "先清理这些行。"
            )
        with op.batch_alter_table("notifications") as batch_op:
            batch_op.drop_constraint("ck_notifications_type", type_="check")
        with op.batch_alter_table("notifications") as batch_op:
            batch_op.create_check_constraint("ck_notifications_type", OLD_CHECK)
    op.drop_index("ix_owner_transfer_history_project_id",
                  "owner_transfer_history")
    op.drop_index("ix_owner_transfer_history_entity_id",
                  "owner_transfer_history")
    op.drop_table("owner_transfer_history")
