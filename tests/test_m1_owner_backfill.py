"""Implementation Plan T1.3/T1.4 · owner 列与存量回填回归测试。

覆盖：
1. T1.3 DDL：tasks.owner_user_id / stories.created_by_user_id / stories.owner_user_id
   三列存在且可空（迁移 q5r6s7t8u9v0）；
2. T1.4 回填（迁移 r6s7t8u9v0w1）：
   - tasks.owner_user_id ← created_by_user_id；
   - stories.created_by_user_id / owner_user_id ← admin；
   - 回填出的 owner 若不在 ProjectMember → 自动补成员行；project 无 owner 时
     把 admin 插成 owner（去掉「或标记」退路，Plan T1.4）；
   - created_by 也为空的 task 回填不了 → 保持 NULL（由 T1.5 执行门 fail closed），
     迁移**不**因此失败；
3. 幂等 / 可回滚：downgrade → upgrade 结果一致；成员行不重复插入。

为什么用「两阶段 alembic」而不是直接跑 init_db：回填迁移必须在**有存量数据**
的库上才有意义。init_db 一路升到 head 时库是空的，跑不出任何回填行为，
测试就成了摆设。

造数用 ORM（避免手写 INSERT 与表结构漂移），再把 owner 列清成 NULL 来模拟
2026-09-02 之前的存量行。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m1_owner_backfill.py -q
"""
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from sqlalchemy import text  # noqa: E402

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, engine  # noqa: E402

# owner 列已落地但尚未回填的挂点
_PRE_BACKFILL = "q5r6s7t8u9v0"


def _alembic_cfg():
    from alembic.config import Config

    cfg = Config(os.path.join(_BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND, "migrations"))
    return cfg


def _alembic_to(target: str) -> None:
    from alembic import command

    cfg = _alembic_cfg()
    # 与 init_db 同款：显式传连接，避免 alembic 自己再建一套 engine
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, target)


@pytest.fixture(scope="module")
def legacy_db():
    """升到「有列无数据」→ 塞存量数据 → 升到 head 触发回填。"""
    _alembic_to(_PRE_BACKFILL)

    with SessionLocal() as s:
        # admin 必须是 is_admin=1：Story 回填的归属目标
        s.execute(text(
            "INSERT INTO users (username, password_hash, display_name, is_admin,"
            " created_at) VALUES ('bk-admin', 'x', 'Admin', 1, CURRENT_TIMESTAMP)"))
        s.commit()

    with SessionLocal() as s:
        p = service.create_project(s, name="legacy P")
        dev = service.register_user(s, username="bk-dev", password="password123")
        epic = service.create_epic(s, project_id=p.id, title="legacy E")
        st = service.create_story(s, epic_id=epic.id, title="legacy S")
        t_a = service.create_task(
            s, project_id=p.id, story_id=st.id, title="legacy T-A", type="dev",
            created_by_user_id=dev.id)
        t_b = service.create_task(s, project_id=p.id, story_id=st.id,
                                  title="legacy T-B", type="dev")
        s.commit()
        ids = (p.id, dev.id, st.id, t_a.id, t_b.id)

    with SessionLocal() as s:
        # 模拟存量：清除归属列 + 清掉成员表（老项目往往一个成员行都没有）
        s.execute(text("DELETE FROM project_members"))
        s.execute(text("UPDATE stories SET created_by_user_id = NULL,"
                       " owner_user_id = NULL"))
        s.execute(text("UPDATE tasks SET owner_user_id = NULL"))
        s.execute(text(
            "UPDATE tasks SET created_by_user_id = NULL WHERE title = 'legacy T-B'"))
        s.commit()

    _alembic_to("head")  # 触发 r6s7t8u9v0w1 回填
    return ids


def _one(sql, **params):
    with SessionLocal() as s:
        return s.execute(text(sql), params).fetchone()


def _admin_id():
    return _one("SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC")[0]


# ---------- 1. T1.3 DDL ----------

def test_owner_columns_exist_and_nullable(legacy_db):
    with SessionLocal() as s:
        cols = {}
        for table in ("tasks", "stories"):
            for row in s.execute(text(f"PRAGMA table_info({table})")).all():
                cols[(table, row[1])] = row  # (cid,name,type,notnull,dflt,pk)
    for key in (("tasks", "owner_user_id"), ("stories", "owner_user_id"),
                ("stories", "created_by_user_id")):
        assert key in cols, f"{key} 列不存在"
        assert cols[key][3] == 0, f"{key} 被建成了 NOT NULL（存量行回填前就是 NULL）"


# ---------- 2. T1.4 回填 ----------

def test_task_owner_backfilled_from_created_by(legacy_db):
    _pid, dev_id, _sid, _ta, _tb = legacy_db
    row = _one("SELECT owner_user_id FROM tasks WHERE title = 'legacy T-A'")
    assert row[0] == dev_id


def test_unattributable_task_keeps_null_owner(legacy_db):
    """created_by 也为空 → 无从推断，保持 NULL，迁移不失败。

    这些 task 会在 T1.5 执行门下 fail closed（匹配不到 agent → 保持待处理），
    需人工补 owner —— 这正是 Plan 决策 c 要的行为。
    """
    row = _one("SELECT owner_user_id FROM tasks WHERE title = 'legacy T-B'")
    assert row[0] is None


def test_story_owner_backfilled_to_admin(legacy_db):
    admin_id = _admin_id()
    row = _one("SELECT created_by_user_id, owner_user_id FROM stories"
               " WHERE title = 'legacy S'")
    assert row == (admin_id, admin_id)


def test_owner_gets_project_membership(legacy_db):
    """回填出的 owner 若不在 ProjectMember → 自动补行（去掉「或标记」退路）。"""
    _pid, dev_id, _sid, _ta, _tb = legacy_db
    dev_member = _one("SELECT role FROM project_members WHERE user_id = :d", d=dev_id)
    assert dev_member is not None
    assert dev_member[0] == "member"

    admin_member = _one("SELECT role FROM project_members WHERE user_id = :a",
                        a=_admin_id())
    assert admin_member is not None
    # 该 project 原本一个 owner 都没有 → admin 被插成 owner，给 T2.2 留接收方
    assert admin_member[0] == "owner"


def test_no_owner_left_outside_project_members(legacy_db):
    """不变量：任何非空 owner 都必须是其 project 的成员。"""
    with SessionLocal() as s:
        bad = s.execute(text(
            "SELECT COUNT(*) FROM tasks t"
            " WHERE t.owner_user_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM project_members pm"
            "                    WHERE pm.project_id = t.project_id"
            "                      AND pm.user_id = t.owner_user_id)")).scalar()
        assert bad == 0


# ---------- 3. 幂等 / 可回滚 ----------

def test_downgrade_and_reupgrade_is_stable(legacy_db):
    from alembic import command

    _pid, dev_id, _sid, _ta, _tb = legacy_db
    cfg = _alembic_cfg()
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        # downgrade 不接受 "a:head" 这类区间写法，只能给单一目标
        command.downgrade(cfg, _PRE_BACKFILL)  # 撤掉回填迁移
        command.upgrade(cfg, "head")           # 再跑一遍

    admin_id = _admin_id()
    assert _one("SELECT owner_user_id FROM tasks"
                " WHERE title = 'legacy T-A'")[0] == dev_id
    assert _one("SELECT owner_user_id FROM tasks"
                " WHERE title = 'legacy T-B'")[0] is None
    assert _one("SELECT created_by_user_id, owner_user_id FROM stories"
                " WHERE title = 'legacy S'") == (admin_id, admin_id)
    # 成员行：downgrade 刻意不删（无法区分「本迁移补的」与「本来就有的」），
    # 重跑后也不应重复插入
    with SessionLocal() as s:
        dupes = s.execute(text(
            "SELECT project_id, user_id, COUNT(*) c FROM project_members"
            " GROUP BY project_id, user_id HAVING c > 1")).all()
        assert dupes == []
