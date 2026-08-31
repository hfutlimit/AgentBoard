"""Worker 心跳 stale-sweep 单测（PR-1 review 收尾）。

覆盖：
1. ``last_heartbeat=None`` 的 active worker → inactive
2. ``last_heartbeat`` 超时的 active worker → inactive
3. ``last_heartbeat`` 仍新鲜的 active worker → 保持 active
4. 已经 inactive 的 worker → 不动（不是 active 状态不入 update 集）
5. ``timeout_seconds <= 0`` → 抛 InvalidValue
6. conditional WHERE 保护并发 fresh heartbeat：sweep 后立刻 register
   一个 fresh 心跳，sweep 不会回滚它（实操上分两次 sweep 验证）

运行：
    cd src/backend-fastapi
    PYTHONPATH=. python -m pytest tests/unit/test_expire_stale_worker_heartbeats.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.core.common.models import Base, utc_now
from agentboard.features.projects.models import Worker
from agentboard.features.projects.service import (
    WORKER_HEARTBEAT_TIMEOUT_SECONDS,
    expire_stale_worker_heartbeats,
)
from agentboard.core.exceptions import InvalidValue


# Per-test in-memory SQLite + create_all —— 绕开 alembic（项目当前
# multiple heads，``init_db()`` 跑不起来；用 conftest.py db_session_override
# 同样的方式建 schema）。
@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _make_worker(s, worker_id: str, status: str = "active",
                 last_heartbeat: datetime | None = None) -> Worker:
    w = Worker(
        worker_id=worker_id,
        hostname="test-host",
        status=status,
        last_heartbeat=last_heartbeat,
    )
    s.add(w)
    s.commit()
    s.refresh(w)
    return w


def test_expire_stale_worker_heartbeats_none_heartbeat_goes_inactive(db_session):
    """last_heartbeat=NULL 的 active worker → inactive。"""
    w = _make_worker(db_session, "w-null-hb", status="active", last_heartbeat=None)
    result = expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    db_session.refresh(w)
    assert result["workers_offline"] >= 1
    assert w.status == "inactive"


def test_expire_stale_worker_heartbeats_old_goes_inactive(db_session):
    """last_heartbeat 超时 → inactive。"""
    old = utc_now() - timedelta(seconds=600)  # 10 min ago, > default 5 min
    w = _make_worker(db_session, "w-old-hb", status="active", last_heartbeat=old)
    result = expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    db_session.refresh(w)
    assert w.status == "inactive"
    assert any(x.worker_id == "w-old-hb" for x in [w]) or result["workers_offline"] >= 1


def test_expire_stale_worker_heartbeats_fresh_stays_active(db_session):
    """last_heartbeat 新鲜 → 保持 active。"""
    fresh = utc_now() - timedelta(seconds=30)  # 30s ago, well within 5 min
    w = _make_worker(db_session, "w-fresh-hb", status="active", last_heartbeat=fresh)
    expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    db_session.refresh(w)
    assert w.status == "active"


def test_expire_stale_worker_heartbeats_already_inactive_untouched(db_session):
    """已 inactive 的 worker → sweep 不动它（不在 update 集里）。"""
    old = utc_now() - timedelta(seconds=600)
    w = _make_worker(db_session, "w-already-inactive", status="inactive", last_heartbeat=old)
    expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    db_session.refresh(w)
    assert w.status == "inactive"
    # 关键：UPDATE 谓词带 status='active'，所以已 inactive 的行不参与 rowcount
    # 这里只断言状态没被改写；不严格要求 rowcount，因为同测试里其他 worker 也会贡献
    # 行数。下面的独立 test 专门隔离断言 rowcount。


def test_expire_stale_worker_heartbeats_rowcount_isolated(db_session):
    """只有 active+stale 的行贡献 rowcount；其他情况不计入。"""
    # 准备：3 active fresh + 2 active stale + 1 inactive stale
    fresh = utc_now() - timedelta(seconds=30)
    stale = utc_now() - timedelta(seconds=600)
    for i in range(3):
        _make_worker(db_session, f"isolated-fresh-{i}", status="active", last_heartbeat=fresh)
    for i in range(2):
        _make_worker(db_session, f"isolated-stale-{i}", status="active", last_heartbeat=stale)
    _make_worker(db_session, "isolated-inactive-stale", status="inactive", last_heartbeat=stale)

    result = expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    # active+stale 是 2 个；inactive-stale 不计入；active+fresh 不计入
    assert result["workers_offline"] == 2


def test_expire_stale_worker_heartbeats_invalid_timeout_raises(db_session):
    """timeout_seconds <= 0 → InvalidValue（不静默回退到全表 inactive）。"""
    with pytest.raises(InvalidValue):
        expire_stale_worker_heartbeats(db_session, timeout_seconds=0)
    with pytest.raises(InvalidValue):
        expire_stale_worker_heartbeats(db_session, timeout_seconds=-1)


def test_expire_stale_worker_heartbeats_concurrent_heartbeat_protected(db_session):
    """Conditional WHERE 保护并发 fresh heartbeat：
    sweep 把 stale 行置 inactive 后，另一个并发路径调 register_worker
    把 last_heartbeat 刷到 now —— 下一次 sweep 不会再回滚它。

    这里通过显式分两步模拟：先 sweep 把它降级，再 register 一次，下一次 sweep
    不应再动它（应当保持 active）。
    """
    from agentboard.features.scheduling.service import register_worker
    old = utc_now() - timedelta(seconds=600)
    w = _make_worker(db_session, "w-concurrent", status="active", last_heartbeat=old)

    # Step 1: sweep 把它降级
    expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    db_session.refresh(w)
    assert w.status == "inactive"

    # Step 2: 模拟 worker 重启 / 重新 register（fresh heartbeat）
    register_worker(db_session, worker_id="w-concurrent", hostname="test-host", status="active")
    db_session.refresh(w)
    assert w.status == "active"
    assert w.last_heartbeat is not None
    # 确保 last_heartbeat 距 now < 5 min
    assert (utc_now() - w.last_heartbeat).total_seconds() < 300

    # Step 3: 下一次 sweep 不应回滚 fresh 的
    expire_stale_worker_heartbeats(db_session, timeout_seconds=300)
    db_session.refresh(w)
    assert w.status == "active"


def test_expire_stale_worker_heartbeats_default_timeout_constant():
    """默认 timeout 与 agent heartbeat 对齐（5 min）；PR-1 决策。"""
    assert WORKER_HEARTBEAT_TIMEOUT_SECONDS == 300
