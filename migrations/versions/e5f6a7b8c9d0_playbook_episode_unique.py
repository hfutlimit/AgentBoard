"""project_playbook_episode: DB 级幂等锚点

Revision ID: e5f6a7b8c9d0
Revises: d7e8f9a0b1c2
Create Date: 2026-08-17

Epic 140 切片 3（project_playbook 真正 DB 级幂等）：

8/15 review P1：``ProjectPlaybook.last_appended_episode_id`` 只记录「最近一次」
追加的 episode，存在「非相邻重复」漏判（episode 101 → 102 → 101 三步走后，
last=102，旧逻辑会让 101 再次追加），并发读旧值同样无法防御。

修复方案：新增 ``project_playbook_episode`` 关联表，
``PRIMARY KEY (project_id, episode_id)`` 作为唯一约束。``update_playbook``
在追加前先 ``INSERT`` 到该表，``IntegrityError``（唯一冲突）即视为已记录、
直接跳过——DB 仲裁在 SQLite / MariaDB 都是强一致，跨 session / 跨线程都安全。

迁移策略：
- 不破坏既有数据：旧 ``last_appended_episode_id`` 字段保留，含义改为「最近一次
  成功追加」展示字段，幂等判据迁移到新表。
- 对存量数据**不**做回填：旧调用方仍可借助字符串兜底 + 新调用方的 DB 约束
  双层保护。
- 无 playbook 时不需要回填（空表自然为空）。
- 后续如需做一致性巡检（找出"已写 playbook 但缺关联表"的孤儿记录），可单
  独跑一次性脚本，与本次迁移解耦。

降级直接 drop table；playbook 内容不受影响。
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d7e8f9a0b1c2"


def upgrade() -> None:
    op.create_table(
        "project_playbook_episode",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("appended_at", sa.DateTime(), nullable=False),
        # 复合主键 = 真正的 DB 级幂等锚点（同 (project, episode) 重复追加触发唯一冲突）
        sa.PrimaryKeyConstraint("project_id", "episode_id", name="pk_project_playbook_episode"),
    )
    op.create_index(
        "ix_project_playbook_episode_episode_id",
        "project_playbook_episode",
        ["episode_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_playbook_episode_episode_id", table_name="project_playbook_episode")
    op.drop_table("project_playbook_episode")
