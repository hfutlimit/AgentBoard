"""Epic 78 Story 105: RunStatus 枚举对齐（DB 与 requirements 文档统一）

背景：代码侧 RunStatus 为 pending|running|success|failed，而 docs/requirements.md
FR-17 写的是 queued|running|succeeded|failed|cancelled，两处不一致；且旧迁移
a5f2e8d9b0c1 建 agent_runs 表时未创建 ck_runs_status CHECK 约束，既有库对
status 列完全无约束（执行器可写任意非法状态）。

本测试验证：
1. RunStatus 枚举为唯一一套取值（含 cancelled 终态）；
2. models.py CHECK 约束与枚举一致（单一事实源）；
3. docs/requirements.md FR-17 与枚举一致；
4. 迁移 k8l9m0n1o2p3 在空库 upgrade head 后真实落下 ck_runs_status 约束，
   cancelled 可写、非法状态被 DB 拒绝。

自包含：临时 SQLite，不依赖 18001 / 18000 / 28080 等外部服务。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent

# 与 enums.py RunStatus 对齐的预期取值（单一事实源）
EXPECTED_RUN_STATUSES = {"pending", "running", "success", "failed", "cancelled"}


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """独立临时 SQLite 库，patch agentboard.database 全局 engine 后跑 Alembic upgrade head。"""
    db_url = f"sqlite:///{tmp_path}/runstatus_test.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    import agentboard.database as db_mod

    new_engine = create_engine(db_url, connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(new_engine, "connect")
    def _fk(dbapi, rec):
        c = dbapi.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    monkeypatch.setattr(db_mod, "engine", new_engine)
    monkeypatch.setattr(db_mod, "SessionLocal",
                        sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True))
    monkeypatch.setattr(db_mod, "URL", db_url)

    # 跑迁移链（含本 Story 新迁移 k8l9m0n1o2p3）
    db_mod.init_db()
    return new_engine


# ---- 1. 枚举一致性 ----

def test_runstatus_enum_unified():
    """RunStatus 全代码库只有一套取值（含 cancelled 终态），无 queued/succeeded 残留。"""
    from agentboard.domains.common.enums import RunStatus

    values = {s.value for s in RunStatus}
    assert values == EXPECTED_RUN_STATUSES
    assert "queued" not in values
    assert "succeeded" not in values


def test_models_check_constraint_matches_enum():
    """domains/scheduling/models.py 的 ck_runs_status 约束与枚举一致（单一事实源）。"""
    from agentboard.domains.common.enums import RunStatus
    from agentboard.domains.scheduling.models import AgentRun

    ck = AgentRun.__table__.constraints
    check_sqls = [str(c.sqltext) for c in ck if c.__class__.__name__ == "CheckConstraint"]
    run_ck = [s for s in check_sqls if "ck_runs_status" in s or "status IN" in s]
    assert run_ck, "agent_runs 表应定义 ck_runs_status CHECK 约束"

    # 从 CHECK SQL 中抽取取值集合，与枚举比对
    import re
    joined = " ".join(run_ck)
    values = set(re.findall(r"'([a-z_]+)'", joined))
    assert values == EXPECTED_RUN_STATUSES


def test_docs_fr17_matches_enum():
    """docs/requirements.md FR-17 的 RunStatus 取值与枚举一致，旧拼写（queued/succeeded）已清除。"""
    text = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    assert "queued|running|succeeded|failed|cancelled" not in text, "FR-17 仍残留旧拼写"
    assert "pending|running|success|failed|cancelled" in text, "FR-17 未同步为统一枚举"


# ---- 2. 迁移真实落约束 ----

def test_migration_creates_check_constraint(tmp_db):
    """upgrade head 后 agent_runs 真实存在 ck_runs_status（含 cancelled）。"""
    from sqlalchemy import inspect

    insp = inspect(tmp_db)
    cks = insp.get_check_constraints("agent_runs")
    run_ck = [c for c in cks if c.get("name") == "ck_runs_status"]
    assert run_ck, "迁移后 agent_runs 应存在 ck_runs_status 约束"
    assert "cancelled" in run_ck[0]["sqltext"]


def test_check_constraint_enforced(tmp_db):
    """约束真实生效：5 个合法值可写、cancelled 可写、非法值被 DB 拒绝。"""
    from sqlalchemy.orm import sessionmaker
    from agentboard.models import AgentRun, AgentSchedule, Project

    Session = sessionmaker(bind=tmp_db, autoflush=False, autocommit=False, future=True)
    s = Session()
    proj = Project(name="p", description="", key="P")
    s.add(proj)
    s.flush()
    sched = AgentSchedule(project_id=proj.id, title="s", schedule_type="cron")
    s.add(sched)
    s.flush()

    # 5 个合法值全部可写（含 Story 105 新增的 cancelled 终态）
    for st in sorted(EXPECTED_RUN_STATUSES):
        s.add(AgentRun(schedule_id=sched.id, status=st))
    s.commit()

    # 非法值被 DB CHECK 拒绝
    s.add(AgentRun(schedule_id=sched.id, status="bogus"))
    with pytest.raises(Exception):
        s.commit()


def test_migration_preserves_columns(tmp_db):
    """batch 表重建不丢列：agent_runs 全列（含 FK/唯一）完整保留。"""
    from sqlalchemy import inspect

    insp = inspect(tmp_db)
    cols = {c["name"] for c in insp.get_columns("agent_runs")}
    assert {"id", "schedule_id", "task_id", "status", "idempotency_key",
            "started_at", "finished_at", "output", "error_message", "created_at"} <= cols

    uqs = insp.get_unique_constraints("agent_runs")
    assert any(u.get("column_names") == ["idempotency_key"] for u in uqs), "idempotency_key 唯一约束丢失"


# ---- 3. 回归：scheduler / executor 引用不破坏 ----

def test_scheduler_and_executor_import_ok(tmp_db):
    """scheduler.py / executor.py 的 RunStatus 引用在新枚举下仍可导入。"""
    import agentboard.scheduler  # noqa: F401
    import agentboard.executor  # noqa: F401
    from agentboard.domains.common.enums import RunStatus

    # executor 使用的语义值仍是有效枚举成员
    for member in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.SUCCESS, RunStatus.FAILED):
        assert member in EXPECTED_RUN_STATUSES
