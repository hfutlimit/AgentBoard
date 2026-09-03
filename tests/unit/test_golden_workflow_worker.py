"""AUTO Story ownership tests for the Python workflow coordinator."""
from __future__ import annotations

from agentboard.core.infrastructure.messaging import rabbitmq as mq
from agentboard.workflow_processor import WorkflowConsumer, WorkflowConsumerConfig


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class _Client:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_kwargs):
        self.calls.append((method, path))
        return _Response()


def test_ticket_requested_internal_event_executes_auto_story_without_cli_agent():
    client = _Client()
    consumer = WorkflowConsumer(WorkflowConsumerConfig(), client=client)
    message = mq.WorkflowMessage(
        event=mq.EVENT_TICKET_REQUESTED,
        entity_type="proposal",
        entity_id=41,
        ref_id=73,
        workload_type="ticket",
    )

    assert consumer.handle_message(message) is True
    assert client.calls == [("POST", "/api/ticket-requests/73/execute")]


def test_workflow_publisher_honors_namespace_env(monkeypatch):
    namespace = "agentboard.workflow.golden.env"
    monkeypatch.setenv("AGENTBOARD_WORKFLOW_NAMESPACE", namespace)

    publisher = mq.WorkflowPublisher()

    assert publisher.topology.exchange == namespace
