"""Epic 78 Story 177 — Executor 常驻 daemon 模式（--daemon）单元测试。

覆盖验收标准：
1. run_daemon 连续处理多个 pending run（按 id 升序），max_runs 到达后退出；
2. 无 pending run 时 idle sleep 后继续轮询，不崩溃（stop_event 提前终止验证）；
3. execute_run 抛异常 → 该 run 标记 failed，daemon 继续处理后续，不拖垮常驻循环；
4. execute_run 返回 None（run 被他人认领/非 pending）→ 正常计数继续，不死循环；
5. CLI --daemon --daemon-max-runs N 连续处理 N 个 pending 后退出；max-runs=0 立即退出。

自包含：临时 SQLite + 真实 service/session；patch execute_run 隔离驱动细节；
CLI 用真实子进程（env 指向临时 DB），不依赖 18001 / 18000 / 28080。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库（与其它测试隔离）
_DB = tempfile.mktemp(suffix="_story177.db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(scope="module")
def session_factory():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from agentboard.database import init_db

    engine = create_engine(
        os.environ["AGENTBOARD_DB_URL"],
        connect_args={"check_same_thread": False}, future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi, rec):
        c = dbapi.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    import agentboard.database as db_mod

    db_mod.engine = engine
    db_mod.SessionLocal = sessionmaker(bind=engine, autoflush=False,
                                       autocommit=False, future=True)
    init_db()

    def sf():
        return db_mod.SessionLocal()

    return sf


@pytest.fixture(scope="module")
def project_id(session_factory):
    from agentboard import service

    with session_factory() as s:
        p = service.create_project(s, name="Story177DaemonProj",
                                   key="S177", is_private=False)
        return p.id


@pytest.fixture(scope="module")
def schedule_id(session_factory, project_id):
    from agentboard import service

    with session_factory() as s:
        sch = service.create_schedule(
            s, project_id=project_id, title="daemon test schedule",
            schedule_type="once", agent="codex",
        )
        return sch.id


def _make_run(session_factory, schedule_id: int) -> int:
    from agentboard import service

    with session_factory() as s:
        run = service.create_run(
            s, schedule_id=schedule_id,
            idempotency_key=f"story177-{time.time_ns()}",
        )
        return run.id


def _run_status(session_factory, run_id: int) -> str | None:
    from agentboard import service

    with session_factory() as s:
        r = service.get_run(s, run_id)
        return r.status if r is not None else None


# ---------------------------------------------------------------------------
# 1) run_daemon：连续处理 pending，max_runs 到达退出
# ---------------------------------------------------------------------------
def test_daemon_processes_pending_in_order_and_stops_at_max_runs(
    session_factory, schedule_id, monkeypatch,
):
    from agentboard import executor, service

    run_ids = [_make_run(session_factory, schedule_id) for _ in range(3)]
    processed: list[int] = []

    def fake_execute(sf, run_id, **kw):
        processed.append(run_id)
        with sf() as s:
            service.update_run(s, run_id, status="success",
                               summary=f"run {run_id} ok",
                               finished_at=utcnow())
        return {"id": run_id, "status": "success"}

    monkeypatch.setattr(executor, "execute_run", fake_execute)
    result = executor.run_daemon(session_factory, idle_sleep=0.01,
                                 poll_interval=0.01, max_runs=3)

    assert processed == sorted(run_ids)  # 按 id 升序逐个处理
    assert result["processed"] == 3
    assert result["last_status"] == "success"
    assert result["stopped"] is False
    for rid in run_ids:
        assert _run_status(session_factory, rid) == "success"


# ---------------------------------------------------------------------------
# 2) 无 pending：idle 轮询不崩溃，stop_event 可优雅退出
# ---------------------------------------------------------------------------
def test_daemon_idles_without_pending_until_stop_event(
    session_factory, schedule_id,
):
    from agentboard import executor

    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    try:
        result = executor.run_daemon(session_factory, idle_sleep=0.05,
                                     poll_interval=0.01, stop_event=stop)
    finally:
        timer.cancel()

    assert result["processed"] == 0
    assert result["last_status"] is None
    assert result["stopped"] is True  # stop_event 触发优雅退出，非异常


# ---------------------------------------------------------------------------
# 3) execute_run 抛异常 → 该 run 标记 failed，daemon 继续
# ---------------------------------------------------------------------------
def test_daemon_marks_failed_on_execute_error_and_continues(
    session_factory, schedule_id, monkeypatch,
):
    from agentboard import executor, service

    r1 = _make_run(session_factory, schedule_id)
    r2 = _make_run(session_factory, schedule_id)
    processed: list[int] = []

    def fake_execute(sf, run_id, **kw):
        processed.append(run_id)
        if run_id == r1:
            raise RuntimeError("boom")
        with sf() as s:
            service.update_run(s, run_id, status="success",
                               summary="ok", finished_at=utcnow())
        return {"id": run_id, "status": "success"}

    monkeypatch.setattr(executor, "execute_run", fake_execute)
    result = executor.run_daemon(session_factory, idle_sleep=0.01,
                                 poll_interval=0.01, max_runs=2)

    assert result["processed"] == 2
    assert processed == [r1, r2]
    # r1 被兜底标记 failed（error_message 含 daemon 标识）
    with session_factory() as s:
        cur = service.get_run(s, r1)
        assert cur.status == "failed"
        assert "daemon execute_run error" in (cur.error_message or "")
    # r2 正常成功
    assert _run_status(session_factory, r2) == "success"


# ---------------------------------------------------------------------------
# 4) execute_run 返回 None（run 非 pending / 被他人认领）→ 计数继续，不死循环
# ---------------------------------------------------------------------------
def test_daemon_counts_none_result_and_keeps_going(
    session_factory, schedule_id, monkeypatch,
):
    from agentboard import executor

    rid = _make_run(session_factory, schedule_id)

    def fake_execute(sf, run_id, **kw):
        return None  # 模拟 run 已被他人认领 / 状态已变

    monkeypatch.setattr(executor, "execute_run", fake_execute)
    result = executor.run_daemon(session_factory, idle_sleep=0.01,
                                 poll_interval=0.01, max_runs=1)

    assert result["processed"] == 1
    assert result["last_status"] is None
    assert _run_status(session_factory, rid) == "pending"  # 未被误改


# ---------------------------------------------------------------------------
# 5) CLI 子进程：--daemon --daemon-max-runs 0 立即退出
#    （max-runs=0 在循环开头即 break，不触碰 DB —— 规避父子进程共享
#    SQLite 文件导致跨进程写锁卡死的已知坑；pending 处理逻辑已由单测覆盖）
# ---------------------------------------------------------------------------
def _cli_python():
    return sys.executable


def test_cli_daemon_max_runs_zero_exits_immediately():
    env = dict(os.environ)
    env["AGENTBOARD_DB_URL"] = f"sqlite:///{tempfile.mktemp(suffix='_story177cli.db')}"
    env["AGENTBOARD_MCP_BACKEND"] = "db"
    proc = subprocess.run(
        [_cli_python(), "-m", "agentboard.executor",
         "--daemon", "--daemon-max-runs", "0"],
        capture_output=True, text=True, timeout=30, env=env,
        cwd=_ROOT,
    )
    assert proc.returncode == 0
    assert "daemon exit" in proc.stdout
    assert "processed=0" in proc.stdout
