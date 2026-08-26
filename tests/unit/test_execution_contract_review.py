"""Regression tests for execution contract hardening."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentboard.agent_runtime.config import AgentDecision, WorkerConfig
from agentboard.agent_runtime.contract import (
    ExecutionResult,
    ExecutionStatus,
    UnknownWorkTypeError,
    WorkType,
)
from agentboard.agent_runtime.coordinator import WorkerCoordinator
from agentboard.agent_runtime.invokers import CallableAgentInvoker


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


def test_coordinator_reuses_handler_instances_for_both_registries():
    config = WorkerConfig(agent="test", agent_cmd="echo test")
    coordinator = WorkerCoordinator(
        config,
        invoker=CallableAgentInvoker(lambda _: AgentDecision(action="noop")),
        client=_Client(),
    )
    assert coordinator.registry[WorkType.IMPLEMENTATION] is coordinator._handlers_by_name["story"]
    assert coordinator.registry[WorkType.PROPOSAL_CLARIFY] is coordinator._handlers_by_name["clarify"]
    coordinator.close()
