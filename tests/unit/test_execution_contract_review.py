"""Regression tests for execution contract hardening."""
from __future__ import annotations

import pytest
import httpx
from pydantic import ValidationError

from agentboard.processors.config import (
    AgentDecision,
    PermanentAgentError,
    TransientAgentError,
    ProcessorConfig,
)
from agentboard.processors.contract import (
    ExecutionResult,
    ExecutionStatus,
    UnknownWorkTypeError,
    WorkType,
)
from agentboard.processors.coordinator import ProcessorCoordinator
from agentboard.processors.invokers import CallableProcessorInvoker
from agentboard.processors.errors import is_transient_execution_error
from agentboard.core.infrastructure.messaging import MessageRetry


class _Client:
    def request(self, method, path, **kwargs):  # pragma: no cover - not called
        raise AssertionError(f"unexpected request: {method} {path}")

    def close(self):
        pass


def test_unknown_work_type_fails_closed():
    with pytest.raises(UnknownWorkTypeError):
        WorkType.canonical_for("implementaton")
    with pytest.raises(UnknownWorkTypeError):
        WorkType.canonical_for(None)
    assert WorkType.canonical_for(WorkType.TASK_IMPLEMENT) is WorkType.IMPLEMENTATION


def test_execution_result_rejects_unknown_action_and_has_typed_status():
    result = ExecutionResult.success("exec-1", "ask")
    assert result.status is ExecutionStatus.SUCCESS
    assert result.action == "ask"
    assert ExecutionResult.skipped("exec-2").status is ExecutionStatus.SKIPPED
    with pytest.raises((ValidationError, ValueError)):
        ExecutionResult.success("exec-3", "made_up_action")


def test_execution_result_preserves_transient_and_permanent_taxonomy():
    transient = ExecutionResult.transient_failure("exec-transient", "upstream timeout")
    permanent = ExecutionResult.permanent_failure("exec-permanent", "invalid agent output")

    assert transient.status is ExecutionStatus.FAILED_TRANSIENT
    assert permanent.status is ExecutionStatus.FAILED_PERMANENT
    assert ExecutionResult.from_exception(
        "exec-timeout", TransientAgentError("timeout"),
    ).status is ExecutionStatus.FAILED_TRANSIENT
    assert ExecutionResult.from_exception(
        "exec-config", PermanentAgentError("missing command"),
    ).status is ExecutionStatus.FAILED_PERMANENT


def test_execution_error_classifier_distinguishes_http_and_agent_failures():
    request = httpx.Request("GET", "http://test")
    assert is_transient_execution_error(
        httpx.HTTPStatusError("server error", request=request, response=httpx.Response(503, request=request))
    )
    assert not is_transient_execution_error(
        httpx.HTTPStatusError("bad request", request=request, response=httpx.Response(400, request=request))
    )


def test_coordinator_requeues_transient_messages_with_a_bound(monkeypatch):
    import agentboard.processors.coordinator as coordinator_module

    coordinator = ProcessorCoordinator.__new__(ProcessorCoordinator)
    coordinator._msg_retries = {}
    monkeypatch.setattr(coordinator_module, "WORKFLOW_RETRY_BACKOFF_SECONDS", (0,))
    key = ("task.available", "task", 12, 0)
    result = ExecutionResult.transient_failure("exec-retry", "temporary outage")

    with pytest.raises(MessageRetry):
        coordinator._message_consumed(result, key)
    assert coordinator._msg_retries[key] == 1
    assert coordinator._message_consumed(result, key) is False


def test_coordinator_reuses_handler_instances_for_both_registries():
    config = ProcessorConfig(agent="test", agent_cmd="echo test")
    coordinator = ProcessorCoordinator(
        config,
        invoker=CallableProcessorInvoker(lambda _: AgentDecision(action="noop")),
        client=_Client(),
    )
    assert coordinator.registry[WorkType.IMPLEMENTATION] is coordinator._handlers_by_name["story"]
    assert coordinator.registry[WorkType.PROPOSAL_CLARIFY] is coordinator._handlers_by_name["clarify"]
    coordinator.close()
