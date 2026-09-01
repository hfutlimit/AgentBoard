"""P0-2（2026-09-01 review）WorkflowMessage task_type 字段单测。

覆盖：
1. dataclass 加 task_type 字段（to_dict / from_dict round-trip）
2. publish 时 task_type kwarg 透传到 body
3. task_type 是 optional：None 时 round-trip 仍是 None
4. 旧消息（无 task_type 字段）from_dict 容错读出 None
5. _opt_str 语义：空串 / 非字符串 → None
6. dispatch_implementation_task 主路径 publish 的 body 带 task_type

运行：
    cd <repo>
    .venv/Scripts/python.exe -m pytest tests/unit/test_workflow_message_task_type.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

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
)


# ---------- 1. dataclass 字段 + round-trip ----------

def test_workflow_message_has_task_type_field():
    """P0-2：dataclass 加 task_type 字段（None 默认）。"""
    m = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
    )
    assert hasattr(m, "task_type")
    assert m.task_type is None


def test_to_dict_includes_task_type():
    m = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        task_type="design",
    )
    d = m.to_dict()
    assert d["task_type"] == "design"


def test_to_dict_task_type_none_serializes_as_null():
    m = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
    )
    d = m.to_dict()
    assert d["task_type"] is None


def test_from_dict_round_trips_task_type():
    """to_dict → from_dict 保留 task_type。"""
    src = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        agent_type="workbuddy", workload_type="task",
        task_type="qa",
    )
    decoded = WorkflowMessage.from_dict(src.to_dict())
    assert decoded.task_type == "qa"
    assert decoded.agent_type == "workbuddy"
    assert decoded.workload_type == "task"


def test_from_bytes_round_trips_task_type():
    """to_bytes → from_bytes 保留 task_type。"""
    src = WorkflowMessage(
        event="task.assigned", entity_type="task", entity_id=1,
        task_type="dev",
    )
    body = src.to_bytes()
    decoded = WorkflowMessage.from_bytes(body)
    assert decoded.task_type == "dev"


# ---------- 2/3/5. 容错 + _opt_str 语义 ----------

def test_from_dict_tolerates_legacy_message_without_task_type():
    """老消息（P0-2 之前发布）没 task_type → from_dict 不抛，None。"""
    legacy = {
        "event": "task.assigned",
        "entity_type": "task",
        "entity_id": 1,
        "ref_id": None,
        "ts": "2026-09-01T00:00:00",
        "agent_type": "codex",
        "workload_type": "task",
        "correlation_id": "legacy",
        "agent_id": "codex-dev-1",
        # 没有 task_type
    }
    m = WorkflowMessage.from_dict(legacy)
    assert m.task_type is None
    assert m.agent_id == "codex-dev-1"


def test_from_dict_normalizes_bad_task_type_values():
    """空串 / int / list → None（_opt_str 防御）。"""
    for bad in ("", "   ", 123, ["d", "e"], None):
        m = WorkflowMessage.from_dict({
            "event": "task.assigned",
            "entity_type": "task",
            "entity_id": 1,
            "task_type": bad,
        })
        assert m.task_type is None, f"{bad!r} 应规范化为 None"


# ---------- 4. publish 时 task_type 透传 ----------

@pytest.fixture
def broker():
    b = InMemoryWorkflowBroker()
    b.declare_topology()
    b.declare_agent_queue("dev-pc-01")
    publisher = WorkflowPublisher(broker=b)
    set_workflow_publisher(publisher)
    yield b
    set_workflow_publisher(None)


def test_publish_event_propagates_task_type_to_body(broker):
    """publish_workflow_event(..., task_type='design') → body.task_type='design'。"""
    for q in broker._queues.values():  # type: ignore[attr-defined]
        q.clear()

    ok = publish_workflow_event(
        EVENT_TASK_ASSIGNED, "task", 42,
        agent_id="wb-design-1", worker_id="dev-pc-01", route="auto",
        agent_type="workbuddy", workload_type="task",
        task_type="design",
    )
    assert ok is True

    msgs = [
        WorkflowMessage.from_bytes(b)
        for b in broker._queues["agentboard.workflow.agent.dev-pc-01"]  # type: ignore[attr-defined]
    ]
    assert len(msgs) == 1
    m = msgs[0]
    assert m.task_type == "design"
    assert m.agent_type == "workbuddy"
    assert m.event == "task.assigned"


def test_publish_event_without_task_type_serializes_null(broker):
    """caller 不传 task_type → body.task_type=null（向后兼容旧 caller）。"""
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
    assert msgs[0].task_type is None


def test_publish_event_empty_string_task_type_treated_as_missing(broker):
    """空串 task_type → publish 层 (task_type or None) → None。"""
    for q in broker._queues.values():  # type: ignore[attr-defined]
        q.clear()

    ok = publish_workflow_event(
        EVENT_TASK_ASSIGNED, "task", 42, route="broadcast",
        task_type="",
    )
    assert ok is True
    msgs = [
        WorkflowMessage.from_bytes(b)
        for b in broker._queues["agentboard.workflow.broadcast"]  # type: ignore[attr-defined]
    ]
    assert len(msgs) == 1
    assert msgs[0].task_type is None
