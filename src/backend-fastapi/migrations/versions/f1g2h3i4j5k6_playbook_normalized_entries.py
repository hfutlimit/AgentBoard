"""project_playbook_episode: normalized entry 表 + project_playbook 去 content_md

Revision ID: f1g2h3i4j5k6
Revises: e5f6a7b8c9d0
Create Date: 2026-08-17

8/17 review P1 #2 长期方案 — ProjectPlaybook 真正解决并发 lost update：

**背景**：
旧版 ``ProjectPlaybook.content_md`` 同时承担「存储」+「展示」双重职责。
多 session 并发追加 playbook pattern 时，read-modify-write 的中间态
必然产生 lost update——last writer 赢，pattern 静默丢失，且无法用
anchor 表补回（anchor 已落库 = 视作「已处理」，但 content 实际丢了）。

**变更**：
1. ``project_playbook``：删除 ``content_md`` 列；``version`` 字段
   保留（语义改为「已追加 entry 数」，由 entries 表 INSERT 自然驱动，
   不再受 read-modify-write 竞争影响）。
2. ``project_playbook_episode``：从「纯 anchor 关联表」升级为
   **normalized entry 表**——
   - 去掉 ``(project_id, episode_id)`` 复合主键，改为
     ``UNIQUE (project_id, episode_id)`` 约束（保留幂等语义）+ 自增
     ``id`` 单 PK（让 entry 可独立 update / delete / 排序）。
   - 新增 ``task_type`` / ``outcome`` / ``summary`` / ``weight`` 字段：
     取代旧 markdown 字符串拼接，结构化存储每条 playbook pattern。
   - ``outcome`` 收紧为 ``('success', 'failure')``（与 ALL_PLAYBOOK_OUTCOMES
     对齐；旧 ``fail`` / ``failed`` 拼写由 service 层 ``_normalize_outcome``
     兼容转换）。
   - ``appended_at`` 保留并重命名为 ``created_at``。

3. **历史 content_md 数据迁移**：旧版已写入的 content_md（非空）会作为
   一条 ``task_type='legacy' / outcome='success' / weight=0.5`` 的
   entry 落表——保留历史 pattern 可见性，但失去结构化粒度（无法按
   task_type / outcome 拆解）。这是有损降级，换来并发安全；新写入
   的 entry 全部结构化。

**降级**：``downgrade`` 重建 content_md 字符串拼接，data loss 不可逆；
若已部署应避免 downgrade。
"""
from alembic import op
import sqlalchemy as sa


revision = "f1g2h3i4j5k6"
down_revision = "e5f6a7b8c9d0"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    is_sqlite = bind.dialect.name == "sqlite"

    # ---- 1) project_playbook_episode: 升级为 normalized entry 表 ----
    # 用 batch_alter_table 兼容 SQLite（不能直接 ALTER 加 PK / drop PK）。
    with op.batch_alter_table("project_playbook_episode") as batch_op:
        # (a) 旧 (project_id, episode_id) 复合主键降级
        batch_op.drop_constraint("pk_project_playbook_episode", type_="primary")
        # (b) 加自增 id 单 PK
        batch_op.add_column(sa.Column(
            "id", sa.Integer(), primary_key=True, autoincrement=True,
        ))
        # (c) 加 entry 结构化字段（设 server_default 让 NOT NULL 在 SQLite batch 里可加）
        batch_op.add_column(sa.Column(
            "task_type", sa.String(length=20), nullable=False, server_default="dev",
        ))
        batch_op.add_column(sa.Column(
            "outcome", sa.String(length=20), nullable=False, server_default="success",
        ))
        batch_op.add_column(sa.Column(
            "summary", sa.Text(), nullable=False, server_default="",
        ))
        batch_op.add_column(sa.Column(
            "weight", sa.Float(), nullable=False, server_default="1.0",
        ))
        # (d) appended_at → created_at 重命名
        batch_op.alter_column(
            "appended_at", new_column_name="created_at",
            existing_type=sa.DateTime(), existing_nullable=False,
        )

    # 重新打开 batch 给同一张表加约束（部分后端不能在同一个 batch 内重建约束）
    with op.batch_alter_table("project_playbook_episode") as batch_op:
        # (e1) episode_id 改为 nullable：弱幂等路径 + legacy 迁移都需要 NULL。
        batch_op.alter_column(
            "episode_id", existing_type=sa.Integer(), nullable=True,
        )
        # (e2) (project_id, episode_id) 降级为 UniqueConstraint
        batch_op.create_unique_constraint(
            "uq_project_playbook_episode_project_episode",
            ["project_id", "episode_id"],
        )
        # (f) outcome CheckConstraint
        batch_op.create_check_constraint(
            "ck_project_playbook_episode_outcome",
            "outcome IN ('success', 'failure')",
        )

    # ---- 2) project_playbook: 历史 content_md → legacy entry 迁移 ----
    # 必须**在 drop content_md 之前**做：扫每个有非空 content_md 的
    # project，插一条 task_type='legacy' 的 entry，summary 保留原
    # markdown 全文。**有损降级**——失去结构化粒度，但保留可见性。
    #
    # episode_id 走 NULL：旧 content_md 里的 pattern 没有逐条对应 task
    # 的信息（旧的 read-modify-write 早把这层结构化信息丢光了），强行
    # 用 last_appended_episode_id 当 episode_id 会误导后续 recall 把它
    # 当成「具体某次完成的 pattern」——用 NULL + task_type='legacy' 更
    # 诚实。SQLite / MariaDB 唯一约束对 NULL 不参与比较，多个 legacy
    # entry 不会冲突。
    has_playbook_table = inspector.has_table("project_playbook")
    if has_playbook_table:
        if is_sqlite:
            op.execute(
                sa.text(
                    """
                    INSERT INTO project_playbook_episode
                        (project_id, episode_id, task_type, outcome, summary, weight, created_at)
                    SELECT
                        pb.project_id,
                        NULL AS episode_id,
                        'legacy' AS task_type,
                        'success' AS outcome,
                        '## 历史 pattern 导入（迁移前 content_md）\n' || pb.content_md AS summary,
                        0.5 AS weight,
                        pb.updated_at AS created_at
                    FROM project_playbook pb
                    WHERE pb.content_md IS NOT NULL
                      AND pb.content_md <> ''
                    """
                )
            )
        else:
            op.execute(
                sa.text(
                    """
                    INSERT INTO project_playbook_episode
                        (project_id, episode_id, task_type, outcome, summary, weight, created_at)
                    SELECT
                        pb.project_id,
                        NULL AS episode_id,
                        'legacy' AS task_type,
                        'success' AS outcome,
                        CONCAT('## 历史 pattern 导入（迁移前 content_md）\n', pb.content_md) AS summary,
                        0.5 AS weight,
                        pb.updated_at AS created_at
                    FROM project_playbook pb
                    WHERE pb.content_md IS NOT NULL
                      AND pb.content_md <> ''
                    """
                )
            )

    # ---- 3) project_playbook: drop content_md 列 + version default 收紧 ----
    with op.batch_alter_table("project_playbook") as batch_op:
        batch_op.drop_column("content_md")
        # version 旧 default=1（兼容老版 ProjectPlaybook().version 默认渲染
        # 「0 entries 时 version=1」的怪异语义）。新版语义是「已追加 entry
        # 数」，空 project 时应为 0。改 default 0 跟 model 一致。
        batch_op.alter_column(
            "version", existing_type=sa.Integer(), nullable=False,
            server_default="0",
        )


def downgrade() -> None:
    """降级：重建 content_md 字符串拼接。

    ⚠️ 警告：若新版已用 entry 路径追加过 pattern，downgrade 只会从
    entries 表重新拼接一次 content_md，**不会**回退到升级前的旧 content_md
    （旧值已 drop，无法恢复）。这是不可逆 data loss。
    """
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ---- 1) project_playbook: 重建 content_md ----
    with op.batch_alter_table("project_playbook") as batch_op:
        batch_op.add_column(sa.Column(
            "content_md", sa.Text(), nullable=False, server_default="",
        ))

    # 把现有 entries 拼回 content_md（最新 version 状态）
    # 仅在 entries 存在时执行
    inspector = sa.inspect(bind)
    if inspector.has_table("project_playbook_episode"):
        # 渲染函数复用 _render_entry_md 的逻辑：success → 成功 pattern，failure → 踩坑 pattern
        if is_sqlite:
            concat_expr = "group_concat('## ' || task_type || '：' || CASE outcome WHEN 'success' THEN '成功 pattern' ELSE '踩坑 pattern' END || '\n' || summary, char(10))"
        else:
            concat_expr = "GROUP_CONCAT(CONCAT('## ', task_type, '：', CASE outcome WHEN 'success' THEN '成功 pattern' ELSE '踩坑 pattern' END, '\n', summary) SEPARATOR '\n')"

        op.execute(
            sa.text(
                f"""
                UPDATE project_playbook pb
                SET content_md = COALESCE((
                    SELECT {concat_expr}
                    FROM project_playbook_episode e
                    WHERE e.project_id = pb.project_id
                    ORDER BY e.id ASC
                ), '')
                """
            )
        )

    # ---- 2) project_playbook_episode: 还原 ----
    with op.batch_alter_table("project_playbook_episode") as batch_op:
        batch_op.drop_constraint("ck_project_playbook_episode_outcome", type_="check")
        batch_op.drop_constraint("uq_project_playbook_episode_project_episode", type_="unique")
        batch_op.drop_constraint("pk_project_playbook_episode", type_="primary")
        # created_at → appended_at 回滚
        batch_op.alter_column(
            "created_at", new_column_name="appended_at",
            existing_type=sa.DateTime(), existing_nullable=False,
        )
        # drop 新增字段
        batch_op.drop_column("weight")
        batch_op.drop_column("summary")
        batch_op.drop_column("outcome")
        batch_op.drop_column("task_type")
        batch_op.drop_column("id")
        # 重建复合主键
        batch_op.create_primary_key(
            "pk_project_playbook_episode", ["project_id", "episode_id"]
        )
