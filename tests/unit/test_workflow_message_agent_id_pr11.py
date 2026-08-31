"""PR-11 WorkflowMessage agent_id 字段单测。

覆盖：
1. dataclass 加 agent_id 字段（to_dict / from_dict round-trip）
2. publish 时 agent_id kwarg 透传到 body
3. agent_id 是 optional：None 时 round-trip 仍是 None
4. 旧消息（无 agent_id 字段）也能 from_dict 容错读出 None
5. agent_type 跟 agent_id 区分：同 type 多 agent 区分
6. routing key 不变（PR-5 走 worker_id，agent_id 只在 body）

运行：
    cd <repo>
    PYTHONPATH=src/backend-fastapi python -m pytest tests/unit/test_workflow_message_agent_id_pr11.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
import json

from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
from agentboard.core.infrastructure.messaging.rabbitmq import (
    InMemoryWorkflowBroker,
    WorkflowMessage,
    WorkflowPublisher,
    publish_workflow_event,
    set_workflow_publisher,
)
from agentboard.core.infrastructure.messaging.rabbitmq import (
    EVENT_TASK_ASSIGNED,
    WORKFLOW_EVENTS,
)


# ---------- 1. dataclass 字段 + round-trip ----------

def test_workflow_message_has_agent_id_field():
    """PR-11：dataclass 加 agent_id 字段（None 默认）。"""
    m = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
    )
    assert hasattr(m, "agent_id")
    assert m.agent_id is None


def test_to_dict_includes_agent_id():
    m = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        agent_id="codex-dev-1",
    )
    d = m.to_dict()
    assert d["agent_id"] == "codex-dev-1"


def test_to_dict_agent_id_none_serializes_as_null():
    m = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
    )
    d = m.to_dict()
    assert d["agent_id"] is None


def test_from_dict_round_trips_agent_id():
    """to_dict → from_dict 保留 agent_id。"""
    src = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        agent_type="codex", workload_type="task", correlation_id="abc",
        agent_id="codex-dev-1",
    )
    decoded = WorkflowMessage.from_dict(src.to_dict())
    assert decoded.agent_id == "codex-dev-1"
    assert decoded.agent_type == "codex"
    assert decoded.workload_type == "task"
    assert decoded.correlation_id == "abc"


def test_from_dict_tolerates_legacy_message_without_agent_id():
    """老消息（PR-11 之前发布）没 agent_id 字段 → from_dict 不抛，agent_id=None。"""
    legacy = {
        "event": "task.assigned",
        "entity_type": "task",
        "entity_id": 1,
        "ref_id": None,
        "ts": "2026-08-31T00:00:00",
        "agent_type": "codex",
        "workload_type": "task",
        "correlation_id": "legacy",
        # 没有 agent_id
    }
    m = WorkflowMessage.from_dict(legacy)
    assert m.agent_id is None
    assert m.agent_type == "codex"
    assert m.correlation_id == "legacy"


def test_from_bytes_round_trips_agent_id():
    """to_bytes → from_bytes 保留 agent_id。"""
    src = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        agent_id="workbuddy-designer-2",
    )
    body = src.to_bytes()
    decoded = WorkflowMessage.from_bytes(body)
    assert decoded.agent_id == "workbuddy-designer-2"


# ---------- 2. publish 时 agent_id 透传 ----------

@pytest.fixture
def broker():
    b = InMemoryWorkflowBroker()
    b.declare_topology()
    b.declare_agent_queue("dev-pc-01")
    publisher = WorkflowPublisher(broker=b)
    set_workflow_publisher(publisher)
    yield b
    set_workflow_publisher(None)


def test_publish_event_propagates_agent_id_to_body(broker):
    """PR-11：publish_workflow_event(..., agent_id='x') 让 body.agent_id='x'。"""
    # 清 queue bodies（但保留 binding）
    for q in broker._queues.values():  # type: ignore[attr-defined]
        q.clear()

    ok = publish_workflow_event(
        EVENT_TASK_ASSIGNED, "task", 42,
        ref_id=99, agent_id="codex-dev-1",
        agent_type="codex", worker_id="dev-pc-01", route="auto",
    )
    assert ok is True

    # 验 worker queue 收到
    msgs = [
        WorkflowMessage.from_bytes(b)
        for b in broker._queues["agentboard.workflow.agent.dev-pc-01"]  # type: ignore[attr-defined]
    ]
    assert len(msgs) == 1
    m = msgs[0]
    assert m.agent_id == "codex-dev-1"
    assert m.agent_type == "codex"
    assert m.event == "task.assigned"


def test_publish_event_without_agent_id_serializes_null(broker):
    """caller 不传 agent_id → body.agent_id=null（向后兼容旧 caller）。"""
    for q in broker._queues.values():  # type: ignore[attr-defined]
        q.clear()

    ok = publish_workflow_event(
        EVENT_TASK_ASSIGNED, "task", 42, route="broadcast",
    )
    assert ok is True
    msgs = [
        WorkflowMessage.from_bytes(b)
        for b in broker._queues["agentboard.workflow.broadcast"]  # type: ignore[attr-defined]
    ]
    assert len(msgs) == 1
    assert msgs[0].agent_id is None


# ---------- 3. agent_id 区分同 type 多 agent ----------

def test_same_type_different_agent_id():
    """PR-11 核心价值：codex-dev-1 和 codex-dev-2 都是 agent_type=codex，
    但 agent_id 区分。"""
    a1 = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        agent_type="codex", agent_id="codex-dev-1",
    )
    a2 = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=2,
        agent_type="codex", agent_id="codex-dev-2",
    )
    # agent_type 相同（PR-3 之前只靠这个无法区分）
    assert a1.agent_type == a2.agent_type
    # agent_id 区分（PR-11 之后才能区分）
    assert a1.agent_id != a2.agent_id
    # body 里两个字段独立存在
    assert a1.to_dict()["agent_id"] == "codex-dev-1"
    assert a2.to_dict()["agent_id"] == "codex-dev-2"


# ---------- 4. routing key 仍用 worker_id（PR-5 路线不变）----------

def test_agent_id_does_not_change_routing_key(broker):
    """PR-11 只动 body；routing key 仍走 worker_id（PR-5）。
    publish 时即使有 agent_id，没有 worker_id → 走 agent.{agent_id}（PR-5 行为）。
    """
    # declare agent_id 对应的 agent queue（生产中 .NET worker 启动时 declare）
    broker.declare_agent_queue("codex-dev-1")
    for q in broker._queues.values():  # type: ignore[attr-defined]
        q.clear()

    # 只有 agent_id，没 worker_id → 路由：auto 模式 + agent_id 非空 → 走 agent.{agent_id}
    ok = publish_workflow_event(
        EVENT_TASK_ASSIGNED, "task", 42,
        agent_id="codex-dev-1",  # 没 worker_id
        route="auto",
    )
    assert ok is True
    # 应走 agent.codex-dev-1（因为 agent_id 非空）
    msgs = broker._queues.get("agentboard.workflow.agent.codex-dev-1", [])  # type: ignore[attr-defined]
    assert len(msgs) == 1, f"应路由到 agent.codex-dev-1，实际 {list(broker._queues.keys())}"


def test_worker_id_still_takes_priority_in_routing(broker):
    """PR-5 + PR-11：worker_id 优先于 agent_id 做 routing（与 auto 行为一致）。"""
    for q in broker._queues.values():  # type: ignore[attr-defined]
        q.clear()

    ok = publish_workflow_event(
        EVENT_TASK_ASSIGNED, "task", 42,
        agent_id="codex-dev-1",
        worker_id="dev-pc-01",  # worker_id 优先
        route="auto",
    )
    assert ok is True
    # 应走 dev-pc-01（不是 codex-dev-1）
    msgs = broker._queues.get("agentboard.workflow.agent.dev-pc-01", [])  # type: ignore[attr-defined]
    assert len(msgs) == 1
    # 但 body.agent_id 保留
    m = WorkflowMessage.from_bytes(msgs[0])
    assert m.agent_id == "codex-dev-1"
    assert m.event == "task.assigned"
