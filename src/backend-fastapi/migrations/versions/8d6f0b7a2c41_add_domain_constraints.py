"""add domain constraints and foreign-key indexes

Revision ID: 8d6f0b7a2c41
Revises: b39f1d2c7a10
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d6f0b7a2c41"
down_revision: Union[str, None] = "b39f1d2c7a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        # SQLite 的 batch 模式会重建被其他表引用的表；迁移期间需暂时关闭检查。
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    op.create_index("ix_epics_project_id", "epics", ["project_id"])
    op.create_index("ix_stories_epic_id", "stories", ["epic_id"])
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_story_id", "tasks", ["story_id"])
    op.create_index("ix_tasks_source_spec_id", "tasks", ["source_spec_id"])

    with op.batch_alter_table("epics") as batch:
        batch.create_check_constraint(
            "ck_epics_status",
            "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
        )
    with op.batch_alter_table("stories") as batch:
        batch.create_check_constraint(
            "ck_stories_status",
            "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
        )
    with op.batch_alter_table("tasks") as batch:
        batch.create_check_constraint("ck_tasks_type", "type IN ('task','bug')")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
        )
        batch.create_check_constraint(
            "ck_tasks_priority",
            "priority IN ('highest','high','medium','low','lowest')",
        )
        batch.create_foreign_key(
            "fk_tasks_source_spec_id_tasks", "tasks", ["source_spec_id"], ["id"],
            ondelete="SET NULL",
        )
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_source_spec_id_tasks", type_="foreignkey")
        batch.drop_constraint("ck_tasks_priority", type_="check")
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.drop_constraint("ck_tasks_type", type_="check")
    with op.batch_alter_table("stories") as batch:
        batch.drop_constraint("ck_stories_status", type_="check")
    with op.batch_alter_table("epics") as batch:
        batch.drop_constraint("ck_epics_status", type_="check")

    op.drop_index("ix_tasks_source_spec_id", table_name="tasks")
    op.drop_index("ix_tasks_story_id", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_index("ix_stories_epic_id", table_name="stories")
    op.drop_index("ix_epics_project_id", table_name="epics")
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")
