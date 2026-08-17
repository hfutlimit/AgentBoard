"""project_playbook idempotency anchor: last_appended_episode_id

Revision ID: d7e8f9a0b1c2
Revises: c7d8e9f0a1b2
Create Date: 2026-08-17

Epic 140 切片 3（项目 Playbook 强幂等）：

- 新增 ``project_playbook.last_appended_episode_id`` INT NULL → ``tasks.id``。
  作为「同一 episode 是否已写入 playbook」的唯一锚点，替代旧实现用
  ``entry.strip() in pb.content_md`` 的字符串包含判断（边界脆弱：手动
  trim / markdown 折叠后等价内容字符串不同 → 重复追加）。

- 引入此列的根本原因：playbook 的去重语义是「同一 task 不重复落 pattern」，
  这是**业务级幂等**而非字符串级去重。把幂等键固化到 schema 字段后，
  即使将来 playbook 内容被压缩 / 重排 / 跨任务合并，回填逻辑仍能保证
  「同一 episode 只记一次」。

- 不影响已有数据：默认 NULL，playbook 行为向下兼容（旧字符串去重保留
  作为兜底，新逻辑仅在 anchor 字段非空时启用）。

- ``task_outcome.id`` 已被 ``task_id`` 唯一约束保证一对一，所以
  ``last_appended_episode_id`` 实际语义上等价于「最近一次成功追加的 task_id」。
  这里直接用 task_id 而非 episode_id，因为 playbook entry 本身也是按 task
  维度写的（``task#<id>: <title>``），保持同源。

降级直接 drop_column；不删 playbook 内容。
"""
from alembic import op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "c7d8e9f0a1b2"


def upgrade() -> None:
    with op.batch_alter_table("project_playbook") as batch:
        batch.add_column(
            sa.Column(
                "last_appended_episode_id",
                sa.Integer(),
                nullable=True,
                comment=(
                    "最近一次成功追加的 episode_id（= task_id，task_outcome 一对一）"
                    "；同 episode 重复调 update_playbook 跳过，避免字符串包含去重"
                    "的脆弱性"
                ),
            )
        )
        batch.create_foreign_key(
            "fk_project_playbook_last_episode_tasks",
            "tasks",
            ["last_appended_episode_id"],
            ["id"],
        )
        batch.create_index(
            "ix_project_playbook_last_appended_episode_id",
            ["last_appended_episode_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("project_playbook") as batch:
        batch.drop_index("ix_project_playbook_last_appended_episode_id")
        batch.drop_constraint(
            "fk_project_playbook_last_episode_tasks", type_="foreignkey",
        )
        batch.drop_column("last_appended_episode_id")
