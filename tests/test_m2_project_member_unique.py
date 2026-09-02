"""Implementation Plan T2.0 · ProjectMember 唯一约束 + owner 选取规则回归测试。

``project_members`` 原先只有 ``role IN ('owner','member')`` 一条 CHECK，同一个
user 在一个项目里可以有多行（甚至一行 owner 一行 member）→ 「项目 owner 是谁」
无法确定。T2.2「移除成员 → 移交 project owner」的接收方必须唯一，所以这条
约束是 M2 的前置。

覆盖：
1. 唯一约束生效：(project,user) 重复插入被拒；
2. 存量去重（migration s7t8u9v0w1x2）：混合 role 的重复行收敛成一行，且
   **保留 owner**（留 member 会把 owner 降权，不可逆）；
3. owner 选取规则：joined_at 最早者胜出，结果确定；
4. 多个 owner 是数据异常 → 报出来（WARNING）但不抛异常，别把一处脏污放大
   成「移除成员」整个功能不可用；
5. add_project_member 的重复校验与 DB 约束对齐（服务层先拦，不靠 DB 报错）。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m2_project_member_unique.py -q
"""
import itertools
import logging
import os
import sys
import tempfile
from datetime import datetime

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

# 全局递增序号：保证新建的 project / user 名字唯一（共享模块级库）
_SEQ = itertools.count(1)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, engine  # noqa: E402
from agentboard.features.projects.models import ProjectMember  # noqa: E402

# 唯一约束落地前的挂点（本迁移正是挂在这里）
_PRE_UNIQUE = "r6s7t8u9v0w1"
_UNIQUE_NAME = "uq_project_members_project_user"


def _alembic_cfg():
    from alembic.config import Config

    cfg = Config(os.path.join(_BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND, "migrations"))
    return cfg


def _alembic_to(target: str) -> None:
    from alembic import command

    cfg = _alembic_cfg()
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, target)


def _alembic_down(target: str) -> None:
    """downgrade 到指定版本。

    别写成 ``_alembic_to("base")``：``upgrade`` 对已在目标版本的库是**无操作**，
    不会降级 —— 那样造数时会撞上 head 才有的唯一约束，报 UNIQUE constraint
    failed，而且报错位置在造数行，很难看出是迁移没降。
    """
    from alembic import command

    cfg = _alembic_cfg()
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, target)


DT = datetime  # 简写，造数用


def _add_raw_member(project_id, user_id, role, joined_at):
    """绕过 ORM/service 直接插行 —— 用来造「约束落地前」的脏数据。"""
    with SessionLocal() as s:
        s.execute(text(
            "INSERT INTO project_members (project_id, user_id, role, joined_at)"
            " VALUES (:p, :u, :r, :j)"),
            {"p": project_id, "u": user_id, "r": role, "j": joined_at})
        s.commit()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """模块级先把库建到 head。

    独立成 fixture 是因为 ``_owner_scenario`` 系列测试**不依赖** deduped_db
    的造数，只依赖 schema 存在 —— 混在 deduped_db 里的话，单独跑 owner 测试
    会以 "no such table: projects" 收场。
    """
    _alembic_to("head")


@pytest.fixture(scope="module")
def deduped_db():
    """降到「无约束」→ 造重复行 → 升到 head 触发去重。

    为什么先删掉 ix_project_members_unique：这条唯一索引在迁移
    ``1a2b3c4d5e6f`` 里早就建了，正常走 alembic 的库不可能有重复行。但
    ``create_all`` 建出的表没有它 —— 那才是能长出重复行的真实场景。删掉
    旧索引再塞脏数据，就是精确复刻那种库。

    进入前只降**一步**到 _PRE_UNIQUE：head 上唯一约束由 s7t8u9v0w1x2 提供，
    降一步正好执行它的 downgrade（删 uq_、重建旧索引 ix_），再手动删掉 ix_
    就得到「无约束」状态。
    千万别 downgrade 到 base —— 那会横穿整条迁移链，链上任何一条 downgrade
    坏了都会把测试带崩，而且与本测试无关。
    """
    _alembic_down(_PRE_UNIQUE)
    with engine.connect() as c:
        c.execute(text("DROP INDEX IF EXISTS ix_project_members_unique"))
        c.commit()

    with SessionLocal() as s:
        p = service.create_project(s, name="m2 P")
        alice = service.register_user(s, username="m2-alice",
                                      password="password123")
        bob = service.register_user(s, username="m2-bob", password="password123")
        carol = service.register_user(s, username="m2-carol",
                                      password="password123")
        s.commit()
        ids = (p.id, alice.id, bob.id, carol.id)

    p_id, alice, bob, carol = ids
    # alice：一行 owner + 一行 member（重复，应留 owner）
    _add_raw_member(p_id, alice, "member", DT(2026, 1, 1))
    _add_raw_member(p_id, alice, "owner", DT(2026, 1, 2))
    # bob：三行 member，全同 role（应留 id 最小的）
    _add_raw_member(p_id, bob, "member", DT(2026, 1, 1))
    _add_raw_member(p_id, bob, "member", DT(2026, 1, 2))
    _add_raw_member(p_id, bob, "member", DT(2026, 1, 3))
    # carol：两行 owner（同 role 重复，应留 id 最小的）
    _add_raw_member(p_id, carol, "owner", DT(2026, 1, 1))
    _add_raw_member(p_id, carol, "owner", DT(2026, 1, 2))

    _alembic_to("head")  # 触发 s7t8u9v0w1x2 去重 + 约束规范化
    return ids


def _members(s, project_id, user_id):
    return s.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id,
    ).all()


# ---------- 1. 存量去重 ----------

def test_duplicate_rows_collapsed_to_one(deduped_db):
    p_id, alice, bob, carol = deduped_db
    with SessionLocal() as s:
        for uid in (alice, bob, carol):
            rows = _members(s, p_id, uid)
            assert len(rows) == 1, f"user {uid} 仍有 {len(rows)} 行成员记录"


def test_dedup_keeps_owner_role_not_member(deduped_db):
    """混合 role 时保留 owner —— 留 member 等于把 owner 降权，不可逆。"""
    p_id, alice, bob, carol = deduped_db
    with SessionLocal() as s:
        row = _members(s, p_id, alice)[0]
        assert row.role == "owner"
        # 并且留下的是**最早**的那条 owner 行的时间戳？不 —— 留下的是 id 最小
        # 的 owner 行。alice 的 owner 行只有一条（id 第 2 个插入），role 优先于
        # 插入顺序，所以这里只断言 role，不断言具体 id。


def test_dedup_same_role_keeps_lowest_id(deduped_db):
    p_id, alice, bob, carol = deduped_db
    with SessionLocal() as s:
        # bob 三行 member，保留 id 最小（最早插入）那条
        bobs = _members(s, p_id, bob)
        assert bobs[0].role == "member"
        assert bobs[0].joined_at == DT(2026, 1, 1)
        # carol 两行 owner，同样保留 id 最小那条
        carols = _members(s, p_id, carol)
        assert carols[0].role == "owner"
        assert carols[0].joined_at == DT(2026, 1, 1)


def test_unique_constraint_is_enforced_by_db(deduped_db):
    """ORM 层绕过之后，DB 约束仍能兜住（服务层校验不是唯一防线）。"""
    p_id, alice, bob, carol = deduped_db
    with SessionLocal() as s:
        s.add(ProjectMember(project_id=p_id, user_id=alice, role="member"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


# ---------- 2. 服务层校验 ----------

def test_add_project_member_rejects_duplicate(deduped_db):
    p_id, alice, bob, carol = deduped_db
    with SessionLocal() as s:
        with pytest.raises(service.Duplicate):
            service.add_project_member(s, project_id=p_id, user_id=alice,
                                       role="member")


# ---------- 3. owner 选取规则 ----------
#
# 注意：owner 规则测试**不能用** module 级的 deduped_db 共享数据 —— 前面的
# 测试会增删 owner 行，后面的断言就踩在前人留下的状态上。每个场景自建一套。

def _owner_scenario(label, spec):
    """自建一个全新 project + 成员。spec = [(key, role, joined_at), ...]。

    add_project_member 不收 joined_at（入伙时间由系统定），所以成员行建好后
    单独刷 —— 这里要测的恰恰是「不同 joined_at 下的选取规则」，绕不开。
    """
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"m2 P{n}-{label}")
        uid = {}
        for key, role, _joined in spec:
            u = service.register_user(s, username=f"m2-{label}{n}-{key}",
                                      password="password123")
            service.add_project_member(s, project_id=p.id, user_id=u.id,
                                       role=role)
            uid[key] = u.id
        for key, _role, joined in spec:
            s.query(ProjectMember).filter(
                ProjectMember.project_id == p.id,
                ProjectMember.user_id == uid[key],
            ).update({"joined_at": joined}, synchronize_session=False)
        s.commit()
        return p.id, uid


def test_resolve_project_owner_single_owner():
    """只有一个 owner 时直接返回它。"""
    p_id, uid = _owner_scenario("single", [
        ("a", "owner", DT(2026, 1, 1)),
        ("b", "member", DT(2026, 1, 2)),
    ])
    with SessionLocal() as s:
        assert service.resolve_project_owner(s, p_id) == uid["a"]


def test_resolve_project_owner_picks_earliest_joined(caplog):
    """多个 owner（不同的人）→ 取 joined_at 最早者，并报冲突。"""
    p_id, uid = _owner_scenario("earliest", [
        ("late", "owner", DT(2026, 6, 1)),
        ("early", "owner", DT(2025, 1, 1)),
        ("m", "member", DT(2026, 1, 1)),
    ])
    # 自挂 handler 而不用 caplog：本仓库导入链会动 logging 全局状态
    # （root 级别 / handler），caplog 抓不抓得到取决于执行顺序，不稳。
    # 直接挂在目标 logger 上，断言只依赖被测代码本身。
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    projects_service = sys.modules["agentboard.features.projects.service"]
    capture = _Capture(level=logging.WARNING)
    # 两件事缺一不可：addHandler 只决定「记录给谁」，logger 自身的
    # effective level 才决定「记录会不会产生」。本仓库导入链会把 root 级别
    # 抬高，logger.level=0 时 effective level 跟着抬高，WARNING 直接在
    # Logger.isEnabledFor 就被丢掉 —— 根本到不了任何 handler。
    projects_service.log.addHandler(capture)
    saved_level = projects_service.log.level
    projects_service.log.setLevel(logging.WARNING)
    try:
        with SessionLocal() as s:
            # 不抛异常：多 owner 是数据脏污，不该让「移除成员」连带失败
            owner = service.resolve_project_owner(s, p_id)
    finally:
        projects_service.log.removeHandler(capture)
        projects_service.log.setLevel(saved_level)

    assert owner == uid["early"], "joined_at 最早的 owner 胜出"
    assert any("个 owner" in r.getMessage() for r in records), \
        "多 owner 必须在日志里报出来，否则脏数据永远没人知道"


def test_resolve_project_owner_is_deterministic():
    """同样的输入必须永远得到同样的输出 —— T2.2 的接收方不能随机。"""
    p_id, _uid = _owner_scenario("deterministic", [
        ("a", "owner", DT(2026, 3, 1)),
        ("b", "owner", DT(2026, 3, 1)),   # 同一秒加入 → 靠 id 决胜
        ("c", "owner", DT(2026, 3, 1)),
    ])
    results = set()
    for _ in range(5):
        with SessionLocal() as s:
            results.add(service.resolve_project_owner(s, p_id))
    assert len(results) == 1


def test_resolve_project_owner_no_owner_returns_none():
    """一个 owner 都没有 → None（T1.4 回填会补 admin 兜底）。"""
    p_id, uid = _owner_scenario("noowner", [("a", "member", DT(2026, 1, 1))])
    with SessionLocal() as s:
        assert service.resolve_project_owner(s, p_id) is None


# ---------- 4. 可回滚 ----------

def test_downgrade_and_reupgrade_is_stable(deduped_db):
    """downgrade 恢复旧索引 → 再 upgrade 重建新约束，去重结果不丢行。

    唯一性用**行为**断言而不是查索引名：SQLite 的 batch_alter_table 重建表后
    内联 UNIQUE 会变成无名 autoindex（sqlite_autoindex_*），按名字查永远查
    不到 —— 那是 SQLite 的实现细节，不是约束缺失。
    """
    from alembic import command

    cfg = _alembic_cfg()
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, _PRE_UNIQUE)
        command.upgrade(cfg, "head")

    with SessionLocal() as s:
        names = {r[0] for r in s.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND tbl_name='project_members'")).all()}
    assert "ix_project_members_unique" not in names, \
        "旧的冗余唯一索引应当被收敛掉，而不是留着双份"

    # 行为断言：重复行仍被 DB 拒绝
    p_id, alice, bob, carol = deduped_db
    with SessionLocal() as s:
        s.add(ProjectMember(project_id=p_id, user_id=alice, role="member"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()

    # 重跑后去重结果稳定，没有把保留行也删掉
    with SessionLocal() as s:
        assert len(_members(s, p_id, alice)) == 1
        assert len(_members(s, p_id, bob)) == 1
        assert len(_members(s, p_id, carol)) == 1
