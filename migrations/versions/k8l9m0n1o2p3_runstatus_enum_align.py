"""align agent_runs RunStatus CHECK with unified enum (Epic 78 Story 105)

Revision ID: k8l9m0n1o2p3
Revises: j6k7l8m9n0a1

Epic 78 Story 105「RunStatus 枚举对齐」：

- 统一 RunStatus 为一套取值：pending | running | success | failed | cancelled
  （代码侧 enums.py 权威；docs/requirements.md FR-17 已同步从
  queued|running|succeeded|failed|cancelled 修正为同一套）。
- 根因修复：旧迁移 a5f2e8d9b0c1 建 agent_runs 表时**未创建**
  ck_runs_status CHECK 约束（约束仅存在于 models.py __table_args__），
  导致既有库（SQLite/MariaDB，均由 Alembic upgrade head 构建）对
  status 列完全无约束，执行器可写入任意非法状态。
- 本迁移为 agent_runs 补建 ck_runs_status CHECK 约束（含 cancelled）。
  SQLite 不支持 ALTER TABLE ADD CONSTRAINT → 用 batch_alter_table
  触发表重建（Alembic 自动反射既有列，不丢数据）；
  MariaDB 上 batch_alter_table 走原生 ALTER，同样生效。

双后端兼容；零 REST 契约变更；不触碰端口 18001。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "k8l9m0n1o2p3"
down_revision: Union[str, None] = "j6k7l8m9n0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK_SQL = "status IN ('pending','running','success','failed','cancelled')"


def upgrade() -> None:
    with op.batch_alter_table("agent_runs", copy_from=None) as batch_op:
        batch_op.create_check_constraint("ck_runs_status", CHECK_SQL)


def downgrade() -> None:
    with op.batch_alter_table("agent_runs", copy_from=None) as batch_op:
        batch_op.drop_constraint("ck_runs_status", type_="check")
