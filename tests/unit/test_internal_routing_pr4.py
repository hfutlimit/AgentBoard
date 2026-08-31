"""PR-4 internal routing 单测。

覆盖：
1. WorkflowTopology.internal_routing / internal_queue / internal_pattern
2. InMemoryWorkflowBroker.declare_topology() 声明 internal queue
3. publish_workflow_event(..., route='internal') 投递到 internal_queue
   而不是 broadcast_queue
4. EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED 在 WORKFLOW_EVENTS 白名单
5. default route='auto' 行为不变（agent_id 非空 → agent；空 → broadcast）

运行：
    cd <repo>
    PYTHONPATH=src/backend-fastapi python -m pytest tests/unit/test_internal_routing_pr4.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

from agentboard.core.infrastructure.messaging.rabbitmq import (
    EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED,
    WORKFLOW_EVENTS,
    InMemoryWorkflowBroker,
    WorkflowMessage,
    WorkflowPublisher,
    WorkflowTopology,
    publish_workflow_event,
    set_workflow_publisher,
)


# ---------- 1. WorkflowTopology helpers ----------

def test_topology_internal_helpers():
    topo = WorkflowTopology("agentboard.workflow")
    assert topo.internal_routing("task.review_assignment_needed") == \
        "workflow.internal.task.review_assignment_needed"
    assert topo.internal_queue == "agentboard.workflow.internal"
    assert topo.internal_pattern() == "workflow.internal.#"
    # 与 broadcast / agent 路由不冲突
    assert topo.internal_routing("x") != topo.broadcast_routing("x")
    assert topo.internal_routing("x") != topo.agent_routing("x")


# ---------- 2. InMemoryWorkflowBroker.declare_topology ----------

def test_broker_declares_internal_queue():
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    # internal_queue 已声明
    assert "agentboard.workflow.internal" in broker._queues
    # 与 internal_pattern 绑定
    assert "workflow.internal.#" in broker._bindings["agentboard.workflow.internal"]


# ---------- 3. publish_workflow_event route='internal' ----------

@pytest.fixture
def in_memory_publisher():
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        yield broker
    finally:
        set_workflow_publisher(None)


def _drain_queue(broker: InMemoryWorkflowBroker, queue_name: str) -> list[WorkflowMessage]:
    """从指定 queue 拿出全部已发布消息并反序列化。"""
    out: list[WorkflowMessage] = []
    for body in broker._queues.get(queue_name, []):  # type: ignore[attr-defined]
        out.append(WorkflowMessage.from_bytes(body))
    return out


def test_publish_internal_routes_to_internal_queue(in_memory_publisher):
    """route='internal' → 进 internal_queue，不进 broadcast_queue。"""
    ok = publish_workflow_event(
        EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED, "task", 99,
        ref_id=7, route="internal",
    )
    assert ok is True
    # internal_queue 收到
    internal_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.internal")
    assert len(internal_msgs) == 1
    m = internal_msgs[0]
    assert m.event == "task.review_assignment_needed"
    assert m.entity_id == 99
    assert m.ref_id == 7
    # broadcast_queue 不应收到
    broadcast_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.broadcast")
    assert len(broadcast_msgs) == 0


def test_publish_broadcast_routes_to_broadcast_queue(in_memory_publisher):
    """route='broadcast'（显式）→ 进 broadcast_queue，不进 internal_queue。"""
    ok = publish_workflow_event(
        "task.available", "task", 5, route="broadcast",
    )
    assert ok is True
    broadcast_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.broadcast")
    assert len(broadcast_msgs) == 1
    internal_msgs = _drain_queue(in_memory_publisher, "agentboard.workflow.internal")
    assert len(internal_msgs) == 0


def test_publish_auto_default_still_uses_broadcast_when_no_agent(in_memory_publisher):
    """默认 route='auto'：无 agent_id → broadcast（向后兼容）。"""
    ok = publish_workflow_event("task.available", "task", 1)
    assert ok is True
    assert len(_drain_queue(in_memory_publisher, "agentboard.workflow.broadcast")) == 1
    assert len(_drain_queue(in_memory_publisher, "agentboard.workflow.internal")) == 0


# ---------- 4. EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED 白名单 ----------

def test_internal_event_in_workflow_events_whitelist():
    """EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED 必须在 WORKFLOW_EVENTS 白名单
    内才能通过 publish_workflow_event 校验。"""
    assert EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED in WORKFLOW_EVENTS
    # publish 不能拒绝它
    broker = InMemoryWorkflowBroker()
    broker.declare_topology()
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        ok = publish_workflow_event(
            EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED, "task", 1, route="internal",
        )
        assert ok is True
    finally:
        set_workflow_publisher(None)
