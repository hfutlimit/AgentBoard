"""task/story 归属列：tasks.owner_user_id + stories.created_by_user_id/owner_user_id

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-09-02

Implementation Plan T1.3：把「归属」从「创建者」里拆出来，让移交成为可能。

- ``created_by_*`` **不可变**，是审计语义（谁建的）；
- ``owner_user_id`` **可变**，是当前归属（移交 = 改它，免确认，见 T2.3）。

两列都 nullable：存量行由 **T1.4 的 data migration 单独回填**（DDL 与 data
分开，便于回滚与 dry-run，Plan §六-2）。在 T1.4 跑完前 owner 为 NULL，
T1.5 的执行门对 NULL owner fail closed —— 这个顺序是刻意的：先加列、
再确认回填数据正确、最后才收紧执行门。

只做 DDL，不写任何数据。
"""
from alembic import op
import sqlalchemy as sa


revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_owner_user", "users", ["owner_user_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tasks_owner_user_id", ["owner_user_id"])

    with op.batch_alter_table("stories", schema=None) as batch:
        batch.add_column(
            sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_stories_created_by_user", "users", ["created_by_user_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_stories_owner_user", "users", ["owner_user_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_stories_created_by_user_id", ["created_by_user_id"])
        batch.create_index("ix_stories_owner_user_id", ["owner_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("stories", schema=None) as batch:
        batch.drop_index("ix_stories_owner_user_id")
        batch.drop_index("ix_stories_created_by_user_id")
        batch.drop_constraint("fk_stories_owner_user", type_="foreignkey")
        batch.drop_constraint("fk_stories_created_by_user", type_="foreignkey")
        batch.drop_column("owner_user_id")
        batch.drop_column("created_by_user_id")

    with op.batch_alter_table("tasks", schema=None) as batch:
        batch.drop_index("ix_tasks_owner_user_id")
        batch.drop_constraint("fk_tasks_owner_user", type_="foreignkey")
        batch.drop_column("owner_user_id")
