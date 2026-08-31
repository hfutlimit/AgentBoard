"""WorkflowMessage PR-2 新字段（agent_type / workload_type / correlation_id）单测。

覆盖：
1. to_dict 包含 3 个新字段
2. to_bytes / from_bytes round-trip 保留全部字段（含 explicit 缺省值）
3. from_dict 容忍缺字段（老 publisher 发的消息）
4. from_dict 容忍非字符串类型的字段（防御老 broker 编码）
5. publish_workflow_event 透传 3 个新 kwargs
6. publish_workflow_event correlation_id 缺省自动生成 UUID4
7. publish_workflow_event correlation_id caller 显式传值保留

运行：
    cd <repo>
    PYTHONPATH=src/backend-fastapi python -m pytest tests/unit/test_workflow_message_pr2_fields.py -q
"""
from __future__ import annotations

import os
import sys
import re
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest

from agentboard.core.infrastructure.messaging.rabbitmq import (
    InMemoryWorkflowBroker,
    MQConfig,
    WorkflowMessage,
    WorkflowPublisher,
    publish_workflow_event,
    set_workflow_publisher,
)


# ---------- 1. to_dict 包含新字段 ----------

def test_to_dict_includes_new_fields():
    m = WorkflowMessage(
        event="task.assigned",
        entity_type="task",
        entity_id=42,
        ref_id=7,
        ts="2026-08-31T00:00:00+00:00",
        agent_type="codex",
        workload_type="task",
        correlation_id="corr-123",
    )
    d = m.to_dict()
    assert d["agent_type"] == "codex"
    assert d["workload_type"] == "task"
    assert d["correlation_id"] == "corr-123"
    # 旧字段不破
    assert d["event"] == "task.assigned"
    assert d["entity_type"] == "task"
    assert d["entity_id"] == 42
    assert d["ref_id"] == 7
    assert d["ts"] == "2026-08-31T00:00:00+00:00"


# ---------- 2. round-trip 保留全部 ----------

def test_roundtrip_preserves_all_fields():
    src = WorkflowMessage(
        event="task.ready_for_review",
        entity_type="task",
        entity_id=100,
        ref_id=99,
        agent_type="workbuddy",
        workload_type="review",
        correlation_id="abc-def",
    )
    body = src.to_bytes()
    decoded = WorkflowMessage.from_bytes(body)
    assert decoded.event == src.event
    assert decoded.entity_type == src.entity_type
    assert decoded.entity_id == src.entity_id
    assert decoded.ref_id == src.ref_id
    assert decoded.agent_type == src.agent_type
    assert decoded.workload_type == src.workload_type
    assert decoded.correlation_id == src.correlation_id


def test_roundtrip_optional_fields_default_none_empty():
    """新字段缺省时：agent_type/workload_type 为 None，correlation_id 为空串。"""
    src = WorkflowMessage(
        event="task.available",
        entity_type="task",
        entity_id=1,
    )
    body = src.to_bytes()
    decoded = WorkflowMessage.from_bytes(body)
    assert decoded.agent_type is None
    assert decoded.workload_type is None
    # correlation_id 缺省 "" —— round-trip 后空串；consumer 按需生成
    assert decoded.correlation_id == ""


# ---------- 3. from_dict 容忍老 publisher 缺字段 ----------

def test_from_dict_tolerates_missing_new_fields():
    """老 publisher 发出来的消息没有 agent_type/workload_type/correlation_id。"""
    legacy = {
        "event": "task.available",
        "entity_type": "task",
        "entity_id": 1,
        "ref_id": None,
        "ts": "2026-08-31T00:00:00+00:00",
    }
    m = WorkflowMessage.from_dict(legacy)
    assert m.event == "task.available"
    assert m.agent_type is None
    assert m.workload_type is None
    assert m.correlation_id == ""


# ---------- 4. from_dict 防御性：非字符串/异常类型 ----------

def test_from_dict_tolerates_non_string_new_fields():
    """老/坏 broker 可能把 agent_type 编码成 int / None。视为 None，不抛。"""
    bad = {
        "event": "task.available",
        "entity_type": "task",
        "entity_id": 1,
        "agent_type": 123,  # int，不应抛
        "workload_type": None,
        "correlation_id": ["list", "not", "str"],
    }
    m = WorkflowMessage.from_dict(bad)
    assert m.agent_type is None
    assert m.workload_type is None
    # correlation_id 走 str(...) 转空串也接受
    assert m.correlation_id == ""


def test_from_dict_correlation_id_preserved_as_string():
    m = WorkflowMessage.from_dict({
        "event": "task.available",
        "entity_type": "task",
        "entity_id": 1,
        "correlation_id": "  my-chain  ",
    })
    # 实现上 _opt_str 会 strip；保留 explicit 处理
    # 现在 _opt_str 只处理 agent_type / workload_type，correlation_id 不 strip
    assert m.correlation_id == "  my-chain  "


# ---------- 5/6/7. publish_workflow_event 透传 + correlation_id 自动生成 ----------

@pytest.fixture
def in_memory_publisher():
    """替换 process 级 publisher 为 InMemoryWorkflowBroker；用完清理。"""
    broker = InMemoryWorkflowBroker()
    # 必须先 declare_topology() 让 broadcast queue 绑到 broadcast.* pattern，
    # 否则 publish() 投递的消息没人收。
    broker.declare_topology()
    publisher = WorkflowPublisher(broker=broker)
    set_workflow_publisher(publisher)
    try:
        yield broker
    finally:
        set_workflow_publisher(None)


def _drain(broker: InMemoryWorkflowBroker) -> list[WorkflowMessage]:
    """从 InMemoryWorkflowBroker 拿出全部已发布消息并反序列化。"""
    out: list[WorkflowMessage] = []
    for qname, bodies in broker._queues.items():  # type: ignore[attr-defined]
        for body in bodies:
            out.append(WorkflowMessage.from_bytes(body))
    return out


def test_publish_passes_new_kwargs_through(in_memory_publisher):
    """publish_workflow_event 接受 3 个新 kwargs 并写进 message。"""
    # 不传 agent_id → broadcast routing（fixture 已 declare 广播队列）
    ok = publish_workflow_event(
        "task.assigned", "task", 11,
        agent_type="codex",
        workload_type="task",
        correlation_id="chain-xyz",
    )
    assert ok is True
    msgs = _drain(in_memory_publisher)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.agent_type == "codex"
    assert m.workload_type == "task"
    assert m.correlation_id == "chain-xyz"


def test_publish_auto_generates_correlation_id_when_missing(in_memory_publisher):
    """caller 不传 correlation_id → publisher 自动生成 UUID4。"""
    ok = publish_workflow_event("task.available", "task", 1)
    assert ok is True
    msgs = _drain(in_memory_publisher)
    assert len(msgs) == 1
    cid = msgs[0].correlation_id
    assert cid != ""
    # UUID4 格式校验
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        cid,
    ), f"不是 UUID4: {cid!r}"


def test_publish_preserves_caller_correlation_id(in_memory_publisher):
    """caller 显式传 correlation_id → 保留原值。"""
    custom = "trace-from-proposal-42"
    ok = publish_workflow_event(
        "task.assigned", "task", 1, correlation_id=custom,
    )
    assert ok is True
    msgs = _drain(in_memory_publisher)
    assert msgs[0].correlation_id == custom


def test_publish_empty_string_correlation_id_triggers_autogen(in_memory_publisher):
    """caller 显式传 correlation_id=""（空串）→ 当作未传，自动生成。"""
    ok = publish_workflow_event(
        "task.assigned", "task", 1, correlation_id="",
    )
    assert ok is True
    msgs = _drain(in_memory_publisher)
    cid = msgs[0].correlation_id
    assert cid != ""
    # 仍是 UUID4
    uuid.UUID(cid)


def test_publish_agent_type_and_workload_type_can_be_none(in_memory_publisher):
    """caller 不传 agent_type / workload_type → 落 None（PR-3 会在 consumer 端
    按 task_type_routing 表回填）。"""
    ok = publish_workflow_event("task.available", "task", 1)
    assert ok is True
    msgs = _drain(in_memory_publisher)
    assert msgs[0].agent_type is None
    assert msgs[0].workload_type is None
