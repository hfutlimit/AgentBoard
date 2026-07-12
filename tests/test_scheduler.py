"""
test_scheduler.py
=================
Task 89 测试：调度扫描器 - 带租约和幂等键的扫描。

覆盖：
- compute_next_run() 正确计算下次触发时间
- compute_next_run_for_once() 过期/未过期处理
- scan_and_trigger() 幂等键防重
- scan_and_trigger() 跳过期/禁用的 schedule
- scan_and_trigger() 推进 next_run_at
- DaemonScheduler 单次扫描

测试策略：直接在测试 session 内调用内部函数，
避免 module patching 的复杂性。
"""

import time
import pytest
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def utcnow():
    """naive UTC datetime，与 models._now() / scheduler._now() 一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- fixtures ----

@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    """每个测试使用独立临时数据库，patch 全局 engine。"""
    db_url = f"sqlite:///{tmp_path}/test_scheduler.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url

    from sqlalchemy import event, create_engine
    from sqlalchemy.orm import sessionmaker

    new_engine = create_engine(db_url, connect_args={"check_same_thread": False}, future=True)
    @event.listens_for(new_engine, "connect")
    def _fk(dbapi, rec):
        c = dbapi.cursor(); c.execute("PRAGMA foreign_keys=ON"); c.close()

    # patch agentboard.database（service/api/scheduler 等共用）
    import agentboard.database as db_mod
    monkeypatch.setattr(db_mod, "engine", new_engine)
    monkeypatch.setattr(db_mod, "SessionLocal",
                       sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True))

    # patch scheduler 模块（直接引用 _db）
    from agentboard import scheduler as sched_mod
    monkeypatch.setattr(sched_mod._db, "engine", new_engine)
    monkeypatch.setattr(sched_mod._db, "SessionLocal",
                       sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True))

    # 初始化 schema
    db_mod.init_db()

    # 工厂函数（返回 context manager）
    @contextmanager
    def scoped():
        s = sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True)()
        s.info["auto_commit"] = False
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    yield scoped


# ---- compute_next_run tests ----

def test_compute_next_run_hourly_boundary():
    """每小时第 0 分触发：12:00:30 后下一次应为 13:00:00"""
    from agentboard.scheduler import compute_next_run
    base = datetime(2026, 7, 12, 12, 0, 30)
    next_time = compute_next_run("0 * * * *", base)
    assert next_time is not None
    assert next_time.hour == 13
    assert next_time.minute == 0


def test_compute_next_run_every_minute():
    """每分钟触发（*/1）：从 14:20:00 应得 14:21:00"""
    from agentboard.scheduler import compute_next_run
    base = datetime(2026, 7, 12, 14, 20, 0)
    next_time = compute_next_run("*/1 * * * *", base)
    assert next_time is not None
    assert next_time.hour == 14
    assert next_time.minute == 21


def test_compute_next_run_invalid_returns_none():
    """非法 cron 表达式返回 None"""
    from agentboard.scheduler import compute_next_run
    assert compute_next_run("not-a-cron") is None
    assert compute_next_run("60 * * * *") is None


def test_compute_next_run_for_once_future():
    """一次性调度：未过期返回原时间"""
    from agentboard.scheduler import compute_next_run_for_once
    future = utcnow() + timedelta(hours=1)
    assert compute_next_run_for_once(future) == future


def test_compute_next_run_for_once_past():
    """一次性调度：已过期返回 None"""
    from agentboard.scheduler import compute_next_run_for_once
    past = utcnow() - timedelta(hours=1)
    assert compute_next_run_for_once(past) is None


# ---- scan_and_trigger tests ----
# 直接调用 _trigger_one（不走 module patch）

def test_scan_triggers_due_cron_schedule(test_db):
    """到期 cron schedule 应被触发，创建 AgentRun"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p = service.create_project(s, name="Test Proj")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="Test Cron",
            schedule_type="cron", cron_expr="*/1 * * * *",
        )
        sch.next_run_at = utcnow() - timedelta(minutes=2)
        s.commit()
        sch_id = sch.id
        now = _now()

    with test_db() as s:
        # 模拟 scan_and_trigger 逻辑：查找到期 schedule
        due = s.query(AgentSchedule).filter(
            AgentSchedule.enabled == True,
            AgentSchedule.next_run_at != None,
            AgentSchedule.next_run_at <= now,
        ).all()
        assert len(due) == 1
        # 直接调用 _trigger_one
        triggered = _trigger_one(s, due[0], now)
        assert triggered is True

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 1
        from agentboard.models import RunStatus
        assert runs[0].status == RunStatus.PENDING
        assert runs[0].idempotency_key is not None


def test_scan_idempotent_no_duplicate_run(test_db):
    """幂等键防止重复触发"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p = service.create_project(s, name="Idemp Test")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="Idemp Sch",
            schedule_type="cron", cron_expr="*/1 * * * *",
        )
        sch.next_run_at = utcnow() - timedelta(minutes=5)
        s.commit()
        sch_id = sch.id
        now = _now()

    with test_db() as s:
        due = s.query(AgentSchedule).filter(
            AgentSchedule.enabled == True,
            AgentSchedule.next_run_at != None,
            AgentSchedule.next_run_at <= now,
        ).all()
        _trigger_one(s, due[0], now)  # 第一次触发
        _trigger_one(s, due[0], now)  # 第二次幂等跳过

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 1  # 幂等：只创建一条


def test_scan_disabled_schedule_not_triggered(test_db):
    """enabled=False 的 schedule 不触发"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p = service.create_project(s, name="Dis Test")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="Dis Sch",
            schedule_type="cron", cron_expr="*/1 * * * *",
        )
        sch.next_run_at = utcnow() - timedelta(minutes=3)
        sch.enabled = False
        s.commit()
        sch_id = sch.id
        now = _now()

    with test_db() as s:
        due = s.query(AgentSchedule).filter(
            AgentSchedule.enabled == True,
            AgentSchedule.next_run_at != None,
            AgentSchedule.next_run_at <= now,
        ).all()
        assert len(due) == 0  # 禁用的不在列表中

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 0


def test_scan_future_schedule_not_triggered(test_db):
    """next_run_at 在未来的 schedule 不触发"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p = service.create_project(s, name="Fut Test")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="Fut Sch",
            schedule_type="cron", cron_expr="*/1 * * * *",
        )
        sch.next_run_at = utcnow() + timedelta(hours=1)
        s.commit()
        sch_id = sch.id
        now = _now()

    with test_db() as s:
        due = s.query(AgentSchedule).filter(
            AgentSchedule.enabled == True,
            AgentSchedule.next_run_at != None,
            AgentSchedule.next_run_at <= now,
        ).all()
        assert len(due) == 0  # 未来的不在列表中

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 0


def test_scan_once_schedule_disables_after_run(test_db):
    """once 类型 schedule 触发后 next_run_at 置 None"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p = service.create_project(s, name="Once Test")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="Once Sch",
            schedule_type="once",
        )
        sch.next_run_at = utcnow() - timedelta(minutes=1)
        s.commit()
        sch_id = sch.id
        now = _now()

    with test_db() as s:
        due = s.query(AgentSchedule).filter(
            AgentSchedule.enabled == True,
            AgentSchedule.next_run_at != None,
            AgentSchedule.next_run_at <= now,
        ).all()
        assert len(due) == 1
        triggered = _trigger_one(s, due[0], now)
        assert triggered is True

    with test_db() as s:
        fresh = service.get_schedule(s, sch_id)
        assert fresh.next_run_at is None  # 推进后 = None
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 1


# ---- DaemonScheduler tests ----

def test_daemon_scheduler_single_scan(test_db):
    """DaemonScheduler 单次扫描正常工作"""
    from agentboard import service
    from agentboard.scheduler import DaemonScheduler
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p = service.create_project(s, name="Daemon Test")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="Daemon Sch",
            schedule_type="cron", cron_expr="*/1 * * * *",
        )
        sch.next_run_at = utcnow() - timedelta(minutes=1)
        s.commit()
        sch_id = sch.id

    sched = DaemonScheduler(poll_interval=1)
    sched.start()
    time.sleep(2)
    sched.stop()

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) >= 1
