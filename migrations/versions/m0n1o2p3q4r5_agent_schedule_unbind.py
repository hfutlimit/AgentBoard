"""agent_schedules 绑定松绑（Epic 78 Story 106）

Revision ID: m0n1o2p3q4r5
Revises: l4m5n6o7p8q9

Epic 78 Story 106「AgentSchedule 绑定松绑（项目/Agent 级 + 筛选）」：

- ``agent_schedules`` 新增 ``agent``（String(20)，指定执行 Agent，NULL=env 默认）。
- 新增 ``task_id``（FK tasks，固定任务语义；有值=每次触发跑该 task）。
- 新增筛选字段：``task_priority``（String(10)，最低优先级门槛）、
  ``task_type``（String(10)，task/bug）、``epic_id``（FK epics，仅该 Epic 任务）。

五列均可空，纯增量；既有数据不受影响。
双后端兼容（SQLite / MariaDB）；零 REST 契约破坏；不触碰端口 18001。
"""
from alembic import op
import sqlalchemy as sa

revision = "m0n1o2p3q4r5"
down_revision = "l4m5n6o7p8q9"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    ("agent", sa.String(length=20)),
    ("task_id", sa.Integer()),
    ("task_priority", sa.String(length=10)),
    ("task_type", sa.String(length=10)),
    ("epic_id", sa.Integer()),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("agent_schedules")}
    for name, coltype in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("agent_schedules", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("agent_schedules")}
    for name, _coltype in _NEW_COLUMNS:
        if name in existing:
            op.drop_column("agent_schedules", name)
