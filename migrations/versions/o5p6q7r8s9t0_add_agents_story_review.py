"""Epic 122 S1：Agent 注册表 + Story 评审闭环字段

Revision ID: o5p6q7r8s9t0
Revises: n1o2p3q4r5s6

S1（多 Agent 自动协作闭环 · 里程碑 M1）增量迁移：

1. 新建 ``agents`` 表（Agent 注册表）：agent_id 唯一幂等键、roles/capabilities JSON 串、
   user_id 绑定服务账号（FK users）、online/last_heartbeat 在线态。
2. ``stories`` 表加评审闭环列：
   - ``reviewer_id``（FK users，可空）：被指派评审人；
   - ``review_round``（int default 0）：评审轮次计数（护栏，上限 5）。
3. ``stories.status`` CHECK 约束扩展：新增 ``pending_review`` / ``ready`` 两个评审态。

双后端兼容（SQLite batch_alter_table / MariaDB drop+create constraint）；
纯增量，不重建既有表、不破坏 proposal/task 契约；零新增依赖。
"""
from alembic import op
import sqlalchemy as sa

revision = "o5p6q7r8s9t0"
down_revision = "n1o2p3q4r5s6"
branch_labels = None
depends_on = None

STORY_STATUS_SQL = "status IN ('backlog','todo','in_progress','in_review','verifying','done','pending_review','ready','blocked')"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. agents 表
    if not inspector.has_table("agents"):
        op.create_table(
            "agents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("agent_id", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("roles", sa.String(200), nullable=False, server_default="[]"),
            sa.Column("capabilities", sa.String(500), nullable=False, server_default="[]"),
            sa.Column("cli_command", sa.String(500), nullable=False, server_default=""),
            sa.Column("auth_key", sa.String(100), nullable=False, server_default=""),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_agents_user_id", "agents", ["user_id"])

    # 2. stories 加列
    cols = {c["name"] for c in inspector.get_columns("stories")}
    if "reviewer_id" not in cols:
        op.add_column("stories", sa.Column("reviewer_id", sa.Integer(), nullable=True))
        op.create_index("ix_stories_reviewer_id", "stories", ["reviewer_id"])
    if "review_round" not in cols:
        op.add_column(
            "stories",
            sa.Column("review_round", sa.Integer(), nullable=False, server_default="0"),
        )

    # 3. CHECK 约束更新（SQLite 需 batch 重建表；MariaDB drop+create）
    checks = {c["name"] for c in inspector.get_check_constraints("stories")}
    if "ck_stories_status" in checks:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("stories") as batch_op:
                batch_op.drop_constraint("ck_stories_status", type_="check")
                batch_op.create_check_constraint("ck_stories_status", STORY_STATUS_SQL)
        else:
            op.drop_constraint("ck_stories_status", "stories", type_="check")
            op.create_check_constraint("ck_stories_status", "stories", STORY_STATUS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 恢复旧 CHECK（移除 pending_review / ready）
    checks = {c["name"] for c in inspector.get_check_constraints("stories")}
    if "ck_stories_status" in checks:
        old_sql = "status IN ('backlog','todo','in_progress','in_review','verifying','done')"
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("stories") as batch_op:
                batch_op.drop_constraint("ck_stories_status", type_="check")
                batch_op.create_check_constraint("ck_stories_status", old_sql)
        else:
            op.drop_constraint("ck_stories_status", "stories", type_="check")
            op.create_check_constraint("ck_stories_status", "stories", old_sql)

    # stories 撤列
    cols = {c["name"] for c in inspector.get_columns("stories")}
    if "reviewer_id" in cols:
        op.drop_index("ix_stories_reviewer_id", table_name="stories")
        op.drop_column("stories", "reviewer_id")
    if "review_round" in cols:
        op.drop_column("stories", "review_round")

    # agents 表
    if inspector.has_table("agents"):
        op.drop_index("ix_agents_user_id", table_name="agents")
        op.drop_table("agents")
