"""PR-5 publish worker_id routing 单测。

覆盖：
1. resolve_worker_for_agent：找 online+enabled AgentInstance 的 worker_id
2. resolve_worker_for_agent：没匹配 → None
3. resolve_worker_for_agent：空 agent_id → None
4. resolve_worker_for_agent：多 instance 选 worker_id 升序第一个（稳定可预测）
5. resolve_worker_for_agent：只 online 但 disabled 的不算
6. WorkflowPublisher.publish(worker_id='X') 走 worker_id 路由
7. WorkflowPublisher.publish 同时给 agent_id + worker_id → worker_id 胜
8. WorkflowPublisher.publish 只给 agent_id → 旧行为（agent_id 路由，向后兼容）
9. publish_workflow_event_for_agent 集成：resolve 后 publish，routing 用
   worker_id，body 含 agent_id

运行：
    cd <repo>
    PYTHONPATH=src/backend-fastapi python -m pytest tests/unit/test_publish_worker_routing_pr5.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.core.common.models import Base, utc_now
from agentboard.core.infrastructure.messaging.rabbitmq import (
    InMemoryWorkflowBroker,
    WorkflowMessage,
    WorkflowPublisher,
    set_workflow_publisher,
    publish_workflow_event,
)
from agentboard.features.projects.models import Agent, AgentInstance, Worker
from agentboard.features.scheduling.service import (
    publish_workflow_event_for_agent,
    resolve_worker_for_agent,
)


# Per-test in-memory SQLite + create_all — 绕开 alembic（multiple heads）
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
                 last_heartbeat=None) -> Worker:
    if last_heartbeat is None:
        last_heartbeat = utc_now()
    w = Worker(
        worker_id=worker_id, hostname="test-host", status=status,
        last_heartbeat=last_heartbeat,
    )
    s.add(w); s.commit(); s.refresh(w)
    return w


def _make_agent(s, agent_id: str, user_id: int = 1) -> Agent:
    a = Agent(agent_id=agent_id, name=agent_id, user_id=user_id,
              cli_command="", model="", enabled=True, online=True)
    s.add(a); s.commit(); s.refresh(a)
    return a


def _make_instance(s, worker_id: str, agent_id: str,
                   online: bool = True, enabled: bool = True) -> AgentInstance:
    inst = AgentInstance(
        worker_id=worker_id, agent_id=agent_id,
        cli_command="", model="", auth_key="", enabled=enabled, online=online,
        last_heartbeat=utc_now(),
    )
    s.add(inst); s.commit(); s.refresh(inst)
    return inst


# ---------- 1-5. resolve_worker_for_agent ----------

def test_resolve_worker_returns_online_instance(db_session):
    _make_worker(db_session, "dev-pc-01")
    _make_instance(db_session, "dev-pc-01", "codex-agent", online=True)
    assert resolve_worker_for_agent(db_session, "codex-agent") == "dev-pc-01"


def test_resolve_worker_no_match_returns_none(db_session):
    _make_worker(db_session, "dev-pc-01")
    _make_instance(db_session, "dev-pc-01", "codex-agent", online=True)
    assert resolve_worker_for_agent(db_session, "minimax-agent") is None


def test_resolve_worker_empty_agent_id_returns_none(db_session):
    assert resolve_worker_for_agent(db_session, "") is None
    assert resolve_worker_for_agent(db_session, None) is None


def test_resolve_worker_picks_min_worker_id_when_multiple_online(db_session):
    """多 worker 在线 → 选 worker_id 升序第一个（稳定可预测）。"""
    _make_worker(db_session, "pc-bravo")
    _make_worker(db_session, "pc-alpha")  # 字典序最小
    _make_instance(db_session, "pc-bravo", "codex-agent", online=True)
    _make_instance(db_session, "pc-alpha", "codex-agent", online=True)
    assert resolve_worker_for_agent(db_session, "codex-agent") == "pc-alpha"


def test_resolve_worker_skips_offline_even_if_enabled(db_session):
    """只 enabled 但 offline → 不算可执行。"""
    _make_worker(db_session, "offline-pc")
    _make_instance(db_session, "offline-pc", "codex-agent",
                   online=False, enabled=True)
    assert resolve_worker_for_agent(db_session, "codex-agent") is None


def test_resolve_worker_skips_disabled_even_if_online(db_session):
    """online 但 disabled → 不算可执行（admin 关掉这个 instance）。"""
    _make_worker(db_session, "disabled-pc")
    _make_instance(db_session, "disabled-pc", "codex-agent",
                   online=True, enabled=False)
    assert resolve_worker_for_agent(db_session, "codex-agent") is None


def test_resolve_worker_returns_picked_worker_even_with_stale_heartbeat(db_session):
    """PR-5 不做时间过滤（expire_stale_* 单独负责）。PR-5 只看 online+enabled。"""
    _make_worker(db_session, "old-hb-pc", last_heartbeat=utc_now() - timedelta(hours=1))
    _make_instance(db_session, "old-hb-pc", "codex-agent", online=True)
    assert resolve_worker_for_agent(db_session, "codex-agent") == "old-hb-pc"


# ---------- 6-8. WorkflowPublisher.publish routing ----------

@pytest.fixture
def in_memory_publisher():
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    # 模拟 .NET worker 已声明自己的 agent queue
    broker.declare_agent_queue("dev-pc-01")
    broker.declare_agent_queue("codex-agent")  # 老路径
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        yield broker
    finally:
        set_workflow_publisher(None)


def _drain_queue(broker: InMemoryWorkflowBroker, queue_name: str) -> list[WorkflowMessage]:
    out: list[WorkflowMessage] = []
    for body in broker._queues.get(queue_name, []):  # type: ignore[attr-defined]
        out.append(WorkflowMessage.from_bytes(body))
    return out


def test_publish_worker_id_routes_to_worker_queue(in_memory_publisher):
    """PR-5：传 worker_id → routing key 是 agent.{worker_id}。"""
    ok = publish_workflow_event(
        "task.assigned", "task", 1,
        agent_id="codex-agent", worker_id="dev-pc-01",
    )
    assert ok is True
    worker_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.agent.dev-pc-01")
    assert len(worker_msgs) == 1
    assert worker_msgs[0].agent_type is None  # 没传 agent_type 字段
    # 老 agent_id queue 不应收到
    legacy_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.agent.codex-agent")
    assert len(legacy_msgs) == 0


def test_publish_worker_id_wins_over_agent_id(in_memory_publisher):
    """PR-5：worker_id + agent_id 都给时，worker_id 胜（物理身份优先）。"""
    ok = publish_workflow_event(
        "task.assigned", "task", 1,
        agent_id="codex-agent", worker_id="dev-pc-01",
    )
    assert ok is True
    # worker_id queue 收到
    assert len(_drain_queue(in_memory_publisher, "agentboard.workflow.agent.dev-pc-01")) == 1
    # agent_id queue 不应收到（worker_id 优先）
    assert len(_drain_queue(in_memory_publisher, "agentboard.workflow.agent.codex-agent")) == 0


def test_publish_only_agent_id_uses_legacy_routing(in_memory_publisher):
    """只传 agent_id → 老路径 routing（向后兼容，PR-5 之前代码）。"""
    ok = publish_workflow_event(
        "task.assigned", "task", 1,
        agent_id="codex-agent",  # 不传 worker_id
    )
    assert ok is True
    legacy_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.agent.codex-agent")
    assert len(legacy_msgs) == 1


# ---------- 9. 集成：publish_workflow_event_for_agent ----------

def test_publish_workflow_event_for_agent_resolves_worker(db_session):
    """集成：DB 有 online AgentInstance → publish 走 worker queue。"""
    # 1. setup broker + DB
    _make_worker(db_session, "dev-pc-01")
    _make_instance(db_session, "dev-pc-01", "codex-agent", online=True)
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    broker.declare_agent_queue("dev-pc-01")
    broker.declare_agent_queue("codex-agent")
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        # 2. 走 helper
        ok = publish_workflow_event_for_agent(
            db_session, "task.assigned", "task", 100,
            agent_id="codex-agent", ref_id=42,
        )
        assert ok is True
        # 3. worker_id 队列收到
        worker_msgs = _drain_queue(broker, "agentboard.workflow.agent.dev-pc-01")
        assert len(worker_msgs) == 1
        m = worker_msgs[0]
        # PR-2 shape：body 里 agent_type 字段没值（PR-5 不动 agent_type 字段，
        # 只在 routing key 用 worker_id）。message body 的 agent_id 字段
        # PR-2 之后才加（当前 .NET 已用），Python 端 publish 路径暂未写
        # body 里的 agent_id 字段（保留扩展点）。
        assert m.event == "task.assigned"
        assert m.entity_id == 100
        assert m.ref_id == 42
        # agent_id 队列不应收到
        assert len(_drain_queue(broker, "agentboard.workflow.agent.codex-agent")) == 0
    finally:
        set_workflow_publisher(None)


def test_publish_workflow_event_for_agent_fallback_when_no_worker(db_session):
    """无 online AgentInstance → 走 agent_id 老路由 + warning（向后兼容）。"""
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    broker.declare_agent_queue("codex-agent")
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        # DB 没 online instance
        ok = publish_workflow_event_for_agent(
            db_session, "task.assigned", "task", 200,
            agent_id="codex-agent",
        )
        assert ok is True
        # 回退到 agent_id 老路由（"broken but explicit"）
        legacy_msgs = _drain_queue(broker, "agentboard.workflow.agent.codex-agent")
        assert len(legacy_msgs) == 1
    finally:
        set_workflow_publisher(None)


def test_publish_workflow_event_for_agent_no_agent_id_broadcasts(db_session):
    """无 agent_id → 不 resolve，直接 broadcast。"""
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        ok = publish_workflow_event_for_agent(
            db_session, "task.available", "task", 1,
            agent_id=None,  # 显式无 agent_id
        )
        assert ok is True
        broadcast_msgs = _drain_queue(broker, "agentboard.workflow.broadcast")
        assert len(broadcast_msgs) == 1
    finally:
        set_workflow_publisher(None)
