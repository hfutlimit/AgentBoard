"""T1.4 回填 dry-run：只读，输出 owner 回填的影响面，不改任何数据。

上线前先跑这个脚本，确认数字可接受再执行 alembic 迁移
（``r6s7t8u9v0w1_backfill_owner_columns``）。

用法：
    PYTHONPATH=src/backend-fastapi python scripts/backfill-owner-columns-dryrun.py
    AGENTBOARD_DB_URL=sqlite:///... python scripts/backfill-owner-columns-dryrun.py

输出指标（与迁移内日志一致，便于对账）：
  - Task：总数 / owner 已空且 created_by 非空（可回填）/ 两者都空（回填不了）
  - Story：总数 / 待回填（owner 为空）
  - 回填后 owner 不在 ProjectMember 的行数（= 迁移会新增多少 member 行）
  - 没有任何 role='owner' 成员的 project 数（迁移会把 admin 插成 owner）
  - admin 用户是否存在（不存在 → 迁移会 fail-fast）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src", "backend-fastapi"))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy import text  # noqa: E402

from agentboard.database import SessionLocal  # noqa: E402


def _scalar(s, sql, **params):
    return s.execute(text(sql), params).scalar() or 0


_REQUIRED_COLUMNS = (
    ("tasks", "created_by_user_id"), ("tasks", "owner_user_id"),
    ("stories", "created_by_user_id"), ("stories", "owner_user_id"),
)


def _preflight(s) -> None:
    """目标库必须先有 T1.3 的列，否则查询会以 'no such column' 崩在半路。

    本地 agentboard.db 常年落后于 head，直接跑会撞这个坑，所以先体检再干活。
    """
    # 用 inspector 而不是 PRAGMA：生产是 MariaDB，PRAGMA 只在 SQLite 上有效。
    insp = sa.inspect(s.bind)
    existing_tables = set(insp.get_table_names())
    missing = []
    for table, column in _REQUIRED_COLUMNS:
        if table not in existing_tables:
            missing.append(f"{table}(表不存在)")
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if column not in cols:
            missing.append(f"{table}.{column}")
    if missing:
        raise SystemExit(
            "目标库尚未升级到 q5r6s7t8u9v0（T1.3 加列迁移），缺少列："
            + ", ".join(missing)
            + "\n请先执行：alembic upgrade head（或重启服务触发 init_db）"
        )


def main() -> int:
    with SessionLocal() as s:
        _preflight(s)

        admin_id = s.execute(text(
            "SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1"
        )).scalar()

        tasks_total = _scalar(s, "SELECT COUNT(*) FROM tasks")
        tasks_fillable = _scalar(
            s, "SELECT COUNT(*) FROM tasks"
               " WHERE owner_user_id IS NULL AND created_by_user_id IS NOT NULL")
        tasks_orphan = _scalar(
            s, "SELECT COUNT(*) FROM tasks"
               " WHERE owner_user_id IS NULL AND created_by_user_id IS NULL")

        stories_total = _scalar(s, "SELECT COUNT(*) FROM stories")
        stories_fillable = _scalar(
            s, "SELECT COUNT(*) FROM stories"
               " WHERE owner_user_id IS NULL OR created_by_user_id IS NULL")

        # 回填后「owner 不在 ProjectMember」的行数。Task 走 created_by_user_id，
        # Story 走 admin；两者口径不同，分开统计便于核对迁移新增的 member 行数。
        task_rows = s.execute(text(
            "SELECT t.project_id, t.created_by_user_id, COUNT(*)"
            "  FROM tasks t"
            " WHERE t.owner_user_id IS NULL AND t.created_by_user_id IS NOT NULL"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM project_members pm"
            "      WHERE pm.project_id = t.project_id"
            "        AND pm.user_id = t.created_by_user_id)"
            " GROUP BY t.project_id, t.created_by_user_id"
        )).all()
        task_member_gap_rows = sum(r[2] for r in task_rows)
        task_member_gap_pairs = len(task_rows)

        story_rows = s.execute(text(
            "SELECT DISTINCT e.project_id FROM stories st"
            "  JOIN epics e ON e.id = st.epic_id"
            " WHERE st.owner_user_id IS NULL OR st.created_by_user_id IS NULL"
        )).all()
        if admin_id is not None:
            story_member_gap_projects = sum(
                1 for (pid,) in story_rows
                if s.execute(text(
                    "SELECT 1 FROM project_members"
                    " WHERE project_id = :p AND user_id = :u LIMIT 1"
                ), {"p": pid, "u": admin_id}).scalar() is None
            )
        else:
            story_member_gap_projects = len(story_rows)

        zero_owner_projects = _scalar(
            s, "SELECT COUNT(*) FROM projects p"
               " WHERE NOT EXISTS (SELECT 1 FROM project_members pm"
               "                    WHERE pm.project_id = p.id"
               "                      AND pm.role = 'owner')")

    print("=== T1.4 owner 回填 dry-run ===")
    print(f"admin user id            : {admin_id}"
          f"{'  (缺失！迁移会 fail-fast)' if admin_id is None else ''}")
    print(f"tasks 总数               : {tasks_total}")
    print(f"  owner 空 + created_by 非空（可回填）: {tasks_fillable}")
    print(f"  owner 空 + created_by 也空（回填不了，T1.5 会 fail closed）: {tasks_orphan}")
    print(f"stories 总数             : {stories_total}")
    print(f"  待回填（owner 或 created_by 为空）: {stories_fillable}")
    print("将新增 project_members 行：")
    print(f"  task owner 缺口        : {task_member_gap_pairs} 个 (project,user) 组合"
          f" / 影响 {task_member_gap_rows} 个 task")
    print(f"  story owner(admin) 缺口: {story_member_gap_projects} 个 project")
    print(f"无 role='owner' 成员的 project 数: {zero_owner_projects}"
          "（迁移会把 admin 插成 owner）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
