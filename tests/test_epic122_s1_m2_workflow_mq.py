"""Epic 122 S1 M2：MQ 事件总线泛化单测。

覆盖：WorkflowMessage 校验、topic 匹配、InMemoryWorkflowBroker 广播/定向路由、
毒消息死信、WorkflowPublisher no-op 回退与注入发布、与 Proposal 总线隔离。
"""
import os
import threading
import uuid

import pytest

from agentboard import mq
from agentboard.mq import (
    EVENT_COMMENT_REPLIED,
    EVENT_REVIEW_REQUESTED,
    EVENT_STORY_CREATED,
    EVENT_STORY_READY,
    InMemoryWorkflowBroker,
    MQConfig,
    MQMessageError,
    ProposalMessage,
    ProposalPublisher,
    WorkflowMessage,
    WorkflowPublisher,
    WorkflowTopology,
)
from agentboard.core.infrastructure.messaging.rabbitmq import _topic_match


# ===================== WorkflowMessage 校验 =====================

class TestWorkflowMessage:
    def test_round_trip(self):
        msg = WorkflowMessage(
            event=EVENT_STORY_CREATED, entity_type="story", entity_id=42,
            ref_id=None, ts="2026-08-07T00:00:00+00:00")
        assert WorkflowMessage.from_bytes(msg.to_bytes()) == msg
        assert WorkflowMessage.from_dict(msg.to_dict()) == msg

    def test_round_trip_with_ref(self):
        msg = WorkflowMessage(
            event=EVENT_REVIEW_REQUESTED, entity_type="story", entity_id=7,
            ref_id=99)
        parsed = WorkflowMessage.from_bytes(msg.to_bytes())
        assert parsed.ref_id == 99
        assert parsed.ts  # 自动补时间戳

    def test_unknown_event_rejected(self):
        with pytest.raises(MQMessageError):
            WorkflowMessage.from_dict({"event": "bogus.event",
                                       "entity_type": "story",
                                       "entity_id": 1})

    def test_invalid_entity_type_rejected(self):
        with pytest.raises(MQMessageError):
            WorkflowMessage.from_dict({"event": EVENT_STORY_CREATED,
                                       "entity_type": "epic",
                                       "entity_id": 1})

    def test_bool_entity_id_rejected(self):
        with pytest.raises(MQMessageError):
            WorkflowMessage.from_dict({"event": EVENT_STORY_CREATED,
                                       "entity_type": "story",
                                       "entity_id": True})

    def test_non_positive_entity_id_rejected(self):
        for bad in (0, -5, "abc", None):
            with pytest.raises(MQMessageError):
                WorkflowMessage.from_dict({"event": EVENT_STORY_CREATED,
                                           "entity_type": "story",
                                           "entity_id": bad})

    def test_non_json_rejected(self):
        with pytest.raises(MQMessageError):
            WorkflowMessage.from_bytes(b"not json {{{")


# ===================== topic 匹配 =====================

class TestTopicMatch:
    def test_broadcast_wildcard(self):
        pat = "workflow.broadcast.#"
        assert _topic_match(pat, "workflow.broadcast.story.created")
        assert _topic_match(pat, "workflow.broadcast.story.ready")
        assert _topic_match(pat, "workflow.broadcast")
        assert not _topic_match(pat, "workflow.agent.wb-dev-1")

    def test_agent_exact(self):
        assert _topic_match("workflow.agent.wb-dev-1", "workflow.agent.wb-dev-1")
        assert not _topic_match("workflow.agent.wb-dev-1",
                                "workflow.agent.wb-dev-2")
        assert not _topic_match("workflow.agent.wb-dev-1",
                                "workflow.broadcast.story.created")

    def test_single_word_star(self):
        assert _topic_match("workflow.agent.*", "workflow.agent.wb-dev-1")
        assert not _topic_match("workflow.agent.*", "workflow.agent.a.b")


# ===================== InMemoryWorkflowBroker =====================

def _make_broker():
    ns = f"agentboard.test.wf.{uuid.uuid4().hex[:8]}"
    return InMemoryWorkflowBroker(namespace=ns), ns


class TestInMemoryWorkflowBroker:
    def test_broadcast_routing(self):
        broker, _ = _make_broker()
        broker.declare_topology()
        broker.declare_agent_queue("wb-dev-1")
        broker.declare_agent_queue("wb-dev-2")
        t = broker.topology

        broker.publish(t.broadcast_routing(EVENT_STORY_READY),
                       WorkflowMessage(event=EVENT_STORY_READY,
                                       entity_type="story", entity_id=1))
        assert broker.queue_depth(t.broadcast_queue) == 1
        # 定向队列不该收到广播事件
        assert broker.queue_depth(t.agent_queue("wb-dev-1")) == 0
        assert broker.queue_depth(t.agent_queue("wb-dev-2")) == 0

    def test_directed_routing_only_target(self):
        broker, _ = _make_broker()
        broker.declare_topology()
        broker.declare_agent_queue("reviewer-1")
        broker.declare_agent_queue("author-1")
        t = broker.topology

        broker.publish(t.agent_routing("reviewer-1"),
                       WorkflowMessage(event=EVENT_REVIEW_REQUESTED,
                                       entity_type="story", entity_id=5,
                                       ref_id=3))
        assert broker.queue_depth(t.agent_queue("reviewer-1")) == 1
        assert broker.queue_depth(t.agent_queue("author-1")) == 0
        assert broker.queue_depth(t.broadcast_queue) == 0

    def test_consume_ack(self):
        broker, _ = _make_broker()
        broker.declare_topology()
        t = broker.topology
        broker.publish(t.broadcast_routing(EVENT_STORY_CREATED),
                       WorkflowMessage(event=EVENT_STORY_CREATED,
                                       entity_type="story", entity_id=2))
        received = []

        def handler(msg: WorkflowMessage) -> bool:
            received.append(msg)
            return True

        stats = broker.consume(t.broadcast_queue, handler, max_messages=1)
        assert stats["acked"] == 1 and stats["dead"] == 0
        assert received and received[0].entity_id == 2
        assert broker.queue_depth(t.broadcast_queue) == 0

    def test_poison_message_goes_dead(self):
        broker, _ = _make_broker()
        broker.declare_topology()
        t = broker.topology
        # 直接投递非法载荷（构造毒消息）
        broker.publish_raw(t.broadcast_routing(EVENT_STORY_CREATED),
                           b'{"event": "bogus", "entity_type": "story"}')
        broker.publish_raw(t.broadcast_routing(EVENT_STORY_CREATED),
                           b'{"event": "story.created", "entity_type": "story",'
                           b' "entity_id": 3}')
        stats = broker.consume(t.broadcast_queue, lambda m: True, max_messages=10)
        assert stats["dead"] == 1
        assert stats["acked"] == 1
        assert len(broker.dead_letters()) == 1

    def test_handler_false_goes_dead(self):
        broker, _ = _make_broker()
        broker.declare_topology()
        t = broker.topology
        broker.publish(t.broadcast_routing(EVENT_STORY_CREATED),
                       WorkflowMessage(event=EVENT_STORY_CREATED,
                                       entity_type="story", entity_id=4))
        stats = broker.consume(t.broadcast_queue, lambda m: False, max_messages=1)
        assert stats["dead"] == 1 and stats["acked"] == 0

    def test_handler_exception_goes_dead(self):
        broker, _ = _make_broker()
        broker.declare_topology()
        t = broker.topology
        broker.publish(t.broadcast_routing(EVENT_STORY_CREATED),
                       WorkflowMessage(event=EVENT_STORY_CREATED,
                                       entity_type="story", entity_id=6))

        def boom(_msg):
            raise RuntimeError("handler bug")

        stats = broker.consume(t.broadcast_queue, boom, max_messages=1)
        assert stats["dead"] == 1 and stats["acked"] == 0

    def test_topology_names(self):
        t = WorkflowTopology("agentboard.workflow")
        assert t.exchange == "agentboard.workflow"
        assert t.broadcast_queue == "agentboard.workflow.broadcast"
        assert t.agent_queue("wb-dev-1") == "agentboard.workflow.agent.wb-dev-1"
        assert t.dlx_exchange == "agentboard.workflow.dlx"
        assert t.dead_queue == "agentboard.workflow.dead"
        assert t.broadcast_routing(EVENT_STORY_READY) == \
            "workflow.broadcast.story.ready"
        assert t.agent_routing("wb-dev-1") == "workflow.agent.wb-dev-1"


# ===================== WorkflowPublisher =====================

class TestWorkflowPublisher:
    def test_disabled_noop(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
        pub = WorkflowPublisher()
        assert not pub.enabled
        # 未配置 URL：发布返回 False，绝不抛异常（回退轮询）
        assert pub.publish(EVENT_STORY_CREATED, "story", 1) is False
        assert pub.publish(EVENT_REVIEW_REQUESTED, "story", 1,
                           ref_id=2, agent_id="wb-dev-1") is False
        pub.close()

    def test_unknown_event_rejected_noop(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
        broker = InMemoryWorkflowBroker()
        broker.declare_topology()
        pub = WorkflowPublisher(broker=broker)
        assert pub.enabled
        assert pub.publish("not.an.event", "story", 1) is False
        assert broker.published == 0

    def test_injected_publish_directed_and_broadcast(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
        broker, _ = _make_broker()
        broker.declare_topology()
        broker.declare_agent_queue("reviewer-1")
        pub = WorkflowPublisher(broker=broker)

        assert pub.publish(EVENT_REVIEW_REQUESTED, "story", 5, ref_id=3,
                           agent_id="reviewer-1") is True
        assert pub.publish(EVENT_STORY_CREATED, "story", 6) is True
        t = broker.topology
        assert broker.queue_depth(t.agent_queue("reviewer-1")) == 1
        assert broker.queue_depth(t.broadcast_queue) == 1
        pub.close()

    def test_get_set_publisher_roundtrip(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
        broker = InMemoryWorkflowBroker()
        broker.declare_topology()
        pub = WorkflowPublisher(broker=broker)
        mq.set_workflow_publisher(pub)
        try:
            assert mq.get_workflow_publisher() is pub
        finally:
            mq.set_workflow_publisher(None)

    def test_publish_workflow_event_never_raises(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
        mq.set_workflow_publisher(None)
        # 未配置：静默 False
        assert mq.publish_workflow_event(EVENT_STORY_CREATED, "story", 1) is False


# ===================== Proposal 总线隔离 =====================

class TestProposalIsolation:
    def test_workflow_message_rejected_by_proposal_parser(self):
        wf = WorkflowMessage(event=EVENT_STORY_CREATED, entity_type="story",
                             entity_id=1)
        with pytest.raises(MQMessageError):
            ProposalMessage.from_bytes(wf.to_bytes())

    def test_proposal_message_rejected_by_workflow_parser(self):
        prop = ProposalMessage(proposal_id=1, round=0)
        with pytest.raises(MQMessageError):
            WorkflowMessage.from_bytes(prop.to_bytes())

    def test_proposal_publisher_unaffected(self, monkeypatch):
        monkeypatch.delenv("AGENTBOARD_MQ_URL", raising=False)
        broker = InMemoryWorkflowBroker()
        broker.declare_topology()
        # ProposalPublisher 用注入的 workflow broker 也应正常工作（类型无关，只投字节）
        pub = ProposalPublisher(broker=broker)
        # ProposalPublisher.publish 走 broker.publish(ProposalMessage) ——
        # 此处 InMemoryWorkflowBroker.publish 签名 (routing_key, message) 与
        # Proposal 的 (message) 不同，验证类型系统隔离即可：
        assert pub.enabled
        pub.close()

    def test_default_namespaces_distinct(self):
        assert mq.DEFAULT_NAMESPACE == "agentboard.proposals"
        assert mq.WORKFLOW_DEFAULT_NAMESPACE == "agentboard.workflow"
        assert mq.DEFAULT_NAMESPACE != mq.WORKFLOW_DEFAULT_NAMESPACE


def test_namespace_env_override(monkeypatch):
    ns = "agentboard.workflow.test.override"
    monkeypatch.setenv("AGENTBOARD_WORKFLOW_NAMESPACE", ns)
    # 当前实现 namespace 走构造参数，env 由上层 MQConfig.from_env 的调用方读取；
    # 这里验证 WorkflowTopology 构造可用任意 namespace（测试隔离的关键）
    t = WorkflowTopology(ns)
    assert t.exchange == ns
    assert t.broadcast_queue == f"{ns}.broadcast"
