"""回填 tasks/stories 的 owner 列（T1.4，纯 data migration）

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-09-02

T1.3 只加了列、值全 NULL。本迁移把存量数据填成一致状态，否则 T1.5 的执行门
（判断 ``item.owner_user_id``）会让所有存量 task/story **匹配不到任何 agent
→ 永久待处理**，等于线上停机。

回填规则：
  - tasks.owner_user_id  ← created_by_user_id（创建者即初始归属）
  - stories.created_by_user_id / owner_user_id ← admin（历史 Story 没有创建者
    列，无从推断，一律归 admin；这是 Plan T1.4 的既定决策）

**原子两步**（R8）：两步在同一个事务里，要么都生效要么都不生效——只填 owner
不补 ProjectMember，会让「owner 不是项目成员」这种自相矛盾的状态落库；反之
光补成员不填 owner，存量 item 照样卡死。

  步骤 1  补 ProjectMember：保证每个回填出来的 owner 都是其所属 project 的
          成员；project 一个 owner 都没有时，把 admin 插成 owner（给 T2.2
          「移除成员 → 移交 project owner」留一个确定的接收方）。
  步骤 2  回填 owner 列。

幂等：全部语句带 IS NULL 条件，重复执行只处理剩余行。
上线前先跑 scripts/backfill-owner-columns-dryrun.py 看影响面（日志口径一致）。
"""
from alembic import op
import sqlalchemy as sa

import logging


log = logging.getLogger("alembic.runtime.migration")

revision = "r6s7t8u9v0w1"
down_revision = "q5r6s7t8u9v0"
branch_labels = None
depends_on = None


def _scalar(conn, sql, **params):
    return conn.execute(sa.text(sql), params).scalar() or 0


def upgrade() -> None:
    conn = op.get_bind()

    # 待回填的 Story 集合：Story 没有创建者列，只能归 admin，所以「有没有 admin」
    # 只在**确实需要回填 Story** 时才是硬要求。空库（全新部署 / 测试 init_db）
    # 一行待填数据都没有，此时不该因为没建 admin 就让迁移失败。
    story_projects = [r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT e.project_id FROM stories st"
        "  JOIN epics e ON e.id = st.epic_id"
        " WHERE st.owner_user_id IS NULL OR st.created_by_user_id IS NULL"
    )).all()]

    admin_id = conn.execute(sa.text(
        "SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1"
    )).scalar()
    if admin_id is None and story_projects:
        raise RuntimeError(
            "backfill owner: 有 %s 个 project 的 Story 待回填，但找不到"
            " is_admin=1 的用户，无法确定 Story 归属。请先创建 admin 账号"
            "（默认 wbadmin）再跑本迁移。" % len(story_projects)
        )

    log.info(
        "backfill owner: dry-run 影响面 → tasks 待回填 %s 行 / stories 待回填 %s 行",
        _scalar(conn, "SELECT COUNT(*) FROM tasks"
                      " WHERE owner_user_id IS NULL AND created_by_user_id IS NOT NULL"),
        _scalar(conn, "SELECT COUNT(*) FROM stories"
                      " WHERE owner_user_id IS NULL OR created_by_user_id IS NULL"),
    )

    # ---- 步骤 1：保证 owner ∈ ProjectMember ----
    # 1a. task 的创建者：在其 task 所属 project 里没有成员行 → 补 member
    task_gaps = conn.execute(sa.text(
        "SELECT DISTINCT t.project_id, t.created_by_user_id"
        "  FROM tasks t"
        " WHERE t.owner_user_id IS NULL AND t.created_by_user_id IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM project_members pm"
        "                    WHERE pm.project_id = t.project_id"
        "                      AND pm.user_id = t.created_by_user_id)"
    )).all()
    for project_id, user_id in task_gaps:
        conn.execute(sa.text(
            "INSERT INTO project_members (project_id, user_id, role, joined_at)"
            " VALUES (:p, :u, 'member', CURRENT_TIMESTAMP)"
        ), {"p": project_id, "u": user_id})
    log.info("backfill owner: 为 task owner 补 %s 条 project_members(member)",
             len(task_gaps))

    # 1b. admin：Story 所属 project 里没有 admin 行 → 补。
    #     project 已有 owner 行 → 插 member（不制造多 owner，避免与 T2.0
    #     「多 owner 报冲突」规则打架）；一个 owner 都没有 → 插 owner。
    added_owner = added_member = 0
    for project_id in story_projects:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM project_members"
            " WHERE project_id = :p AND user_id = :u LIMIT 1"
        ), {"p": project_id, "u": admin_id}).scalar()
        if exists:
            continue
        has_owner = conn.execute(sa.text(
            "SELECT 1 FROM project_members"
            " WHERE project_id = :p AND role = 'owner' LIMIT 1"
        ), {"p": project_id}).scalar()
        role = "member" if has_owner else "owner"
        conn.execute(sa.text(
            "INSERT INTO project_members (project_id, user_id, role, joined_at)"
            " VALUES (:p, :u, :r, CURRENT_TIMESTAMP)"
        ), {"p": project_id, "u": admin_id, "r": role})
        if role == "owner":
            added_owner += 1
        else:
            added_member += 1
    log.info("backfill owner: 为 admin 补 %s 条 owner + %s 条 member 成员行",
             added_owner, added_member)

    # ---- 步骤 2：回填 owner 列 ----
    n_tasks = conn.execute(sa.text(
        "UPDATE tasks SET owner_user_id = created_by_user_id"
        " WHERE owner_user_id IS NULL AND created_by_user_id IS NOT NULL"
    )).rowcount
    n_stories = conn.execute(sa.text(
        "UPDATE stories"
        "   SET created_by_user_id = :a, owner_user_id = :a"
        " WHERE owner_user_id IS NULL OR created_by_user_id IS NULL"
    ), {"a": admin_id}).rowcount
    log.info("backfill owner: 完成 tasks %s 行 / stories %s 行", n_tasks, n_stories)

    # 收尾自检：还有回填不了的（created_by 也为空）→ 只告警，不阻断。
    # 这些行会被 T1.5 的执行门 fail closed，需人工补 owner（Plan §六-5）。
    leftover = _scalar(conn, "SELECT COUNT(*) FROM tasks"
                             " WHERE owner_user_id IS NULL")
    if leftover:
        log.warning(
            "backfill owner: 仍有 %s 个 task 的 owner 为空（created_by_user_id"
            " 本身为 NULL，无从推断）。它们会在 T1.5 执行门下保持待处理，"
            " 需人工补 owner。", leftover)


def downgrade() -> None:
    """只回滚回填的列值，不删步骤 1 补的 member 行。

    成员行删了可能把某个 project 打成无 owner 状态，且无法区分「本迁移补的」
    与「本来就有的」——留着无害（多一个成员不会破坏任何不变量），删了有风险。
    """
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE stories SET created_by_user_id = NULL, owner_user_id = NULL"))
    conn.execute(sa.text("UPDATE tasks SET owner_user_id = NULL"))
