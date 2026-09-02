"""project_members 去重 + 唯一约束规范化（T2.0）

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-09-02

背景（与先前的判断不同，以实测为准）
------------------------------------
``project_members`` 的 (project_id, user_id) 唯一性在**数据库里早已存在**：
迁移 ``1a2b3c4d5e6f`` 建过一条唯一索引，名叫 ``ix_project_members_unique``。
但 ORM 模型里**没有**声明它 —— 模型与库不一致。

这个不一致有实际后果：

- 任何走 ``Base.metadata.create_all()`` 的路径（测试夹具、运维脚本、将来的
  工具）建出的表**没有**这条唯一性，于是可以插进重复行；
- SQLAlchemy 的 autogenerate / ``batch_alter_table`` 看不见这条约束，后续
  改表时可能生成与现状冲突的 DDL。

而 ``init_db()`` 是跑 alembic 到 head（不是 create_all），所以生产库走的是
迁移链、索引是在的 —— 这也是为什么「模型没约束」这件事一直没暴露。

本迁移做什么
------------
1. **去重**（防御性）：对 (project_id,user_id) 重复的行保留一行。正常迁移库
   里不可能有重复（唯一索引拦着），这条是为 create_all 建出来的库兜底。
   保留规则：role='owner' 优先（留 member 等于把 owner 降权，不可逆），
   同 role 取 id 最小。
2. **规范化约束名**：删掉 ``ix_project_members_unique``，建
   ``uq_project_members_project_user``，与模型声明、以及库里其它约束的命名
   （``uq_agent_instance_worker_agent`` / ``uq_review_votes_entity_reviewer_agent``）
   保持一致。
   *为什么不直接加第二条*：两条完全相同的唯一索引等于每次 INSERT 多维护一份，
   纯浪费，且模型与库继续对不上。
3. **多 owner 自检**：唯一约束只保证「一个人一行」，防不住「多个不同 user 都
   是 owner」。那种情况 ``resolve_project_owner`` 按 joined_at 最早者处理，
   这里把冲突的 project 打出来，提示人工收敛。

幂等：每条 DDL 前先探查存在性；去重语句用 NOT IN(保留集)，重复跑删 0 行。
"""
from alembic import op
import sqlalchemy as sa

import logging


log = logging.getLogger("alembic.runtime.migration")

revision = "s7t8u9v0w1x2"
down_revision = "r6s7t8u9v0w1"
branch_labels = None
depends_on = None

TABLE = "project_members"
NEW_NAME = "uq_project_members_project_user"   # 与模型声明一致
OLD_NAME = "ix_project_members_unique"         # 1a2b3c4d5e6f 建的旧唯一索引


def _unique_index_columns(conn, table: str) -> list[set[str]]:
    """返回表上所有唯一索引覆盖的列集合。

    按**列集合**而不是按名字判 —— SQLite 的 ``batch_alter_table`` 重建表时，
    内联的 UNIQUE 约束会生成**无名 autoindex**（sqlite_autoindex_*），按名字
    探测永远查不到，会造成「约束明明在、迁移却再建一份」的假象。
    """
    if conn.dialect.name == "sqlite":
        result: list[set[str]] = []
        for _seq, name, unique, _origin, _partial in conn.execute(
            sa.text(f"PRAGMA index_list({table})")
        ).all():
            if not unique:
                continue
            cols = [r[2] for r in conn.execute(
                sa.text(f"PRAGMA index_info({name})")).all()]
            result.append(set(cols))
        return result
    grouped: dict[str, set[str]] = {}
    for index_name, col in conn.execute(sa.text(
        "SELECT index_name, column_name FROM information_schema.statistics"
        " WHERE table_schema = DATABASE() AND table_name = :t"
        "   AND non_unique = 0"
    ), {"t": table}).all():
        grouped.setdefault(index_name, set()).add(col)
    return list(grouped.values())


def _has_unique_on(conn, table: str, cols: tuple[str, ...]) -> bool:
    """表上是否已有覆盖 ``cols`` 的唯一索引（列集合完全一致才算）。"""
    return set(cols) in _unique_index_columns(conn, table)


def _index_exists(conn, name: str) -> bool:
    """按名字查索引（用于区分「唯一性由旧索引提供」还是「由新约束提供」）。

    SQLite 走 sqlite_master，其它走 information_schema。只用来定位具名对象，
    存在性判断一律以 ``_has_unique_on``（按列集合）为准。
    """
    if conn.dialect.name == "sqlite":
        return conn.execute(sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = :n"
        ), {"n": name}).scalar() is not None
    return conn.execute(sa.text(
        "SELECT 1 FROM information_schema.statistics"
        " WHERE table_schema = DATABASE() AND table_name = :t"
        "   AND index_name = :n LIMIT 1"
    ), {"t": TABLE, "n": name}).scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # ---- 步骤 1：去重（防御性，正常迁移库里删 0 行）----
    # 保留集 = 两组 MIN(id) 的并集：
    #   (a) 有 owner 行的组 → 留 id 最小的 owner 行；
    #   (b) 只有 member 行的组 → 留 id 最小的 member 行。
    # 拆成两个 SELECT 而不是一条带 ORDER BY 的相关子查询，是为了让「优先留
    # owner」一眼可读 —— 去重规则错了没法回滚，宁可啰嗦。
    dup_groups = conn.execute(sa.text(
        "SELECT project_id, user_id, COUNT(*) c FROM project_members"
        " GROUP BY project_id, user_id HAVING c > 1"
    )).all()
    if dup_groups:
        log.warning(
            "project_members：%s 个 (project,user) 组合存在重复行，开始去重",
            len(dup_groups))

    deleted = conn.execute(sa.text(
        "DELETE FROM project_members"
        " WHERE id NOT IN ("
        "   SELECT MIN(id) FROM project_members"
        "    WHERE role = 'owner'"
        "    GROUP BY project_id, user_id"
        "   UNION"
        "   SELECT MIN(m.id) FROM project_members m"
        "    WHERE m.role = 'member'"
        "      AND NOT EXISTS (SELECT 1 FROM project_members o"
        "                       WHERE o.project_id = m.project_id"
        "                         AND o.user_id = m.user_id"
        "                         AND o.role = 'owner')"
        "    GROUP BY m.project_id, m.user_id"
        " )"
    )).rowcount
    if deleted:
        log.warning("project_members：删除 %s 行重复成员记录", deleted)

    # ---- 步骤 2：规范化约束名 ----
    # 按列集合判断「唯一性是否已在」，而不是按名字 —— SQLite batch 重建表后
    # UNIQUE 变成无名 autoindex，按名字查会误判成「不存在」然后重复建。
    already = _has_unique_on(conn, TABLE, ("project_id", "user_id"))
    if already and not _index_exists(conn, OLD_NAME):
        # 唯一性在、且不是旧索引（就是 NEW_NAME 本身或无名 autoindex）→ 什么都不做
        log.info("%s 的 (project_id,user_id) 唯一性已存在，跳过", TABLE)
    else:
        if _index_exists(conn, OLD_NAME):
            # 旧索引提供的就是同一列的唯一性 → 收敛：删旧建新，避免双份索引
            with op.batch_alter_table(TABLE) as batch_op:
                batch_op.drop_index(OLD_NAME)
            log.info("已删除旧唯一索引 %s（由 %s 接管）", OLD_NAME, NEW_NAME)
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.create_unique_constraint(NEW_NAME, ["project_id", "user_id"])
        log.info("%s 创建完成", NEW_NAME)

    # ---- 步骤 3：多 owner 自检 ----
    multi = conn.execute(sa.text(
        "SELECT project_id, COUNT(*) c FROM project_members"
        " WHERE role = 'owner' GROUP BY project_id HAVING c > 1"
    )).all()
    for project_id, count in multi:
        log.warning(
            "project %s 有 %s 个 owner（不同的人）—— 唯一约束管不了这种情况。"
            "resolve_project_owner 当前按 joined_at 最早者处理，建议人工收敛成一个",
            project_id, count)


def downgrade() -> None:
    conn = op.get_bind()
    if _has_unique_on(conn, TABLE, ("project_id", "user_id")) \
            and not _index_exists(conn, OLD_NAME):
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_constraint(NEW_NAME, type_="unique")
    # 恢复旧索引，让库回到迁移前的样子
    if not _has_unique_on(conn, TABLE, ("project_id", "user_id")):
        op.create_index(OLD_NAME, TABLE, ["project_id", "user_id"], unique=True)
    # 去重删掉的行不恢复：无从还原，且恢复它们只会重新制造「owner 是谁不确定」。
