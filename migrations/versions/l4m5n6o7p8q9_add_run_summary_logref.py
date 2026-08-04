"""add agent_runs.summary & log_ref (Epic 78 Story 104 结果回写)

Revision ID: l4m5n6o7p8q9
Revises: k8l9m0n1o2p3

Epic 78 Story 104「AgentRun 状态机驱动 + report_run_result」：

- ``agent_runs`` 新增 ``summary``（Text，Agent 主动回写的运行结果摘要，
  由 report_run_result 落库，比单纯 output 更结构化）。
- 新增 ``log_ref``（String(512)，日志/产物引用，如外部存储路径）。

两列均可空，纯增量；既有数据不受影响。
双后端兼容（SQLite / MariaDB）；零 REST 契约破坏；不触碰端口 18001。
"""
from alembic import op
import sqlalchemy as sa

revision = "l4m5n6o7p8q9"
down_revision = "k8l9m0n1o2p3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    if "summary" not in columns:
        op.add_column("agent_runs", sa.Column("summary", sa.Text(), nullable=True))
    if "log_ref" not in columns:
        op.add_column("agent_runs", sa.Column("log_ref", sa.String(length=512), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    if "log_ref" in columns:
        op.drop_column("agent_runs", "log_ref")
    if "summary" in columns:
        op.drop_column("agent_runs", "summary")
