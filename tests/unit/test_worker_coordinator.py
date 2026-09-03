"""Tests for ProcessorCoordinator and Unified Execution Model (Stage 1 & Stage 2)."""
from __future__ import annotations

import httpx
import pytest

from agentboard.processors.config import AgentDecision, ProcessorConfig
from agentboard.processors.contract import (
    ExecutionCommand,
    ExecutionResult,
    WorkType,
)
from agentboard.processors.coordinator import ProcessorCoordinator
from agentboard.processors.handlers import build_work_type_registry
from agentboard.processors.invokers import CallableProcessorInvoker


class DummyClient:
    """Mock client for coordinator tests."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def request(self, method: str, path: str, **kwargs):
        req = httpx.Request(method, f"http://127.0.0.1:58124{path}")
        if method == "POST":
            self.posts.append((path, kwargs.get("json", {})))
            if path.endswith("/claim"):
                return httpx.Response(200, json={"status": "ok"}, request=req)
            if "/review" in path:
                return httpx.Response(200, json={"status": "done"}, request=req)
            if path.endswith("/reclaim-stale"):
                return httpx.Response(200, json={"reclaimed": 0}, request=req)
            if "/comments" in path:
                return httpx.Response(200, json={"id": 1}, request=req)
            return httpx.Response(200, json={}, request=req)
        if method == "GET":
            self.gets.append(path)
            if path == "/api/proposals/pending":
                return httpx.Response(200, json=[{"id": 101, "title": "Test Prop"}], request=req)
            if path == "/api/proposals":
                return httpx.Response(200, json=[], request=req)
            if path == "/api/proposals/1":
                return httpx.Response(200, json={"id": 1, "title": "Test Prop", "content": "Content", "current_round": 1}, request=req)
            if path == "/api/admin/ticket-requests/pending":
                return httpx.Response(200, json=[{"id": 201, "proposal_id": 101, "ticket_type": "task"}], request=req)
            if path == "/api/stories":
                return httpx.Response(200, json={"items": [{"id": 301, "title": "Test Story"}]}, request=req)
            if "/review-context" in path:
                return httpx.Response(200, json={
                    "task": {"id": 401, "title": "Dev Task", "status": "in_review"},
                    "comments": [],
                    "proposal_spec": "Spec",
                    "owner_agent_id": "test_agent",
                }, request=req)
            if path.endswith("/tasks"):
                return httpx.Response(200, json={"items": [{"id": 401, "status": "done"}]}, request=req)
            if path.startswith("/api/epics/"):
                return httpx.Response(200, json={"project_id": 1}, request=req)
            return httpx.Response(200, json={}, request=req)
        if method == "PUT":
            return httpx.Response(200, json={}, request=req)
        return httpx.Response(200, json={}, request=req)

    def close(self):
        pass


def test_coordinator_registry_covers_all_work_types():
    """Verify ProcessorCoordinator registry covers all WorkType enum variants."""
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()
    registry = build_work_type_registry(dummy, config)

    assert WorkType.PROPOSAL_CLARIFY in registry
    assert WorkType.PROPOSAL_CONVERT in registry
    assert WorkType.TASK_IMPLEMENT in registry
    assert WorkType.TASK_REVIEW in registry
    assert WorkType.TASK_RESPOND in registry


def test_coordinator_dispatch_proposal_clarify():
    """Verify dispatching PROPOSAL_CLARIFY command."""
    invoker = CallableProcessorInvoker(lambda ctx: AgentDecision(
        action="ask",
        questions=["Question 1?"],
        summary="Need clarification",
        inspected_files=["src/main.py"],
    ))
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=invoker, client=dummy)
    cmd = ExecutionCommand(
        execution_id="exec_prop_1",
        work_type=WorkType.PROPOSAL_CLARIFY,
        entity_type="proposal",
        entity_id=1,
        context={"id": 1, "title": "Test", "content": "Desc", "current_round": 1},
    )

    result = coord.dispatch(cmd)
    assert result.status == "success"
    assert result.action == "ask"
    assert result.inspected_files == ["src/main.py"]


def test_coordinator_dispatch_task_review():
    """Verify dispatching TASK_REVIEW command."""
    invoker = CallableProcessorInvoker(lambda ctx: AgentDecision(
        action="approve",
        comment="LGTM",
        inspected_files=["src/feature.py"],
    ))
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=invoker, client=dummy)
    cmd = ExecutionCommand(
        execution_id="exec_review_401",
        work_type=WorkType.TASK_REVIEW,
        entity_type="task",
        entity_id=401,
        context={},
    )

    result = coord.dispatch(cmd)
    assert result.status == "success"
    assert result.action == "approve"
    assert result.summary == "LGTM"
    assert result.inspected_files == ["src/feature.py"]
    # Check that review post was recorded
    assert any("/api/tasks/401/review" in path for path, _ in dummy.posts)


def test_coordinator_dispatch_task_implement():
    """Verify dispatching TASK_IMPLEMENT command for a single task."""
    invoker = CallableProcessorInvoker(lambda ctx: AgentDecision(
        action="story_handled",
        summary="Implemented feature and passed tests",
        inspected_files=["src/calc.py", "tests/test_calc.py"],
    ))
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=invoker, client=dummy)
    cmd = ExecutionCommand(
        execution_id="exec_task_impl_501",
        work_type=WorkType.TASK_IMPLEMENT,
        entity_type="task",
        entity_id=501,
        context={"task": {"id": 501, "title": "Implement Calc", "status": "in_progress"}},
    )

    result = coord.dispatch(cmd)
    assert result.status == "success"
    assert result.action == "story_handled"
    assert result.summary == "Implemented feature and passed tests"
    assert len(result.inspected_files) == 2


def test_coordinator_inflight_deduplication():
    """Verify that duplicate in-flight dispatch of the same work item is skipped."""
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=CallableProcessorInvoker(lambda ctx: AgentDecision(action="story_handled")), client=dummy)
    # Manually mark as in-flight
    coord._inflight.add((str(WorkType.TASK_IMPLEMENT), 999))

    cmd = ExecutionCommand(
        execution_id="exec_dup",
        work_type=WorkType.TASK_IMPLEMENT,
        entity_type="task",
        entity_id=999,
    )

    result = coord.dispatch(cmd)
    assert result.status == "skipped"
    assert result.action == "skipped"


def test_coordinator_handle_workflow_message():
    """Verify coordinator translates WorkflowMessage events into appropriate commands."""
    invoker = CallableProcessorInvoker(lambda ctx: AgentDecision(
        action="approve",
        comment="Auto-approved by reviewer",
    ))
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=invoker, client=dummy)

    from agentboard.core.infrastructure.messaging import WorkflowMessage
    msg = WorkflowMessage(
        event="task.review_requested",
        entity_type="task",
        entity_id=401,
    )

    ok = coord.handle_workflow_message(msg)
    assert ok is True


def test_coordinator_poll_once_aggregates_all_domains():
    """Verify coordinator poll_once aggregates work across all domains."""
    invoker = CallableProcessorInvoker(lambda ctx: AgentDecision(
        action="ask" if "proposal_id" in ctx or "content" in ctx else "story_handled",
        summary="Processed",
    ))
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=invoker, client=dummy)
    stats = coord.poll_once()

    assert stats["clarified"] >= 1
    assert "stale_stories" in stats
    assert "stale_tasks" in stats


def test_work_type_from_task_mapping():
    """Verify WorkType.from_task resolves granular types correctly."""
    assert WorkType.from_task("design", is_review=False) == WorkType.DESIGN
    assert WorkType.from_task("design", is_review=True) == WorkType.DESIGN_REVIEW
    assert WorkType.from_task("dev", is_review=False) == WorkType.IMPLEMENTATION
    assert WorkType.from_task("dev", is_review=True) == WorkType.IMPLEMENTATION_REVIEW
    assert WorkType.from_task("qa", is_review=False) == WorkType.QA
    assert WorkType.from_task("qa", is_review=True) == WorkType.QA_REVIEW
    assert WorkType.from_task(None, is_review=False) == WorkType.IMPLEMENTATION


def test_coordinator_dispatch_granular_types():
    """Verify coordinator dispatches granular DESIGN and QA_REVIEW WorkTypes."""
    invoker = CallableProcessorInvoker(lambda ctx: AgentDecision(
        action="approve" if "review" in ctx.get("work_type", "") else "story_handled",
        summary="Processed granular",
    ))
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=invoker, client=dummy)

    # 1. Design implementation
    cmd_design = ExecutionCommand(
        execution_id="exec_design_1",
        work_type=WorkType.DESIGN,
        entity_type="task",
        entity_id=1,
        context={"task": {"id": 1, "title": "Design Arch", "status": "in_progress"}},
    )
    res_design = coord.dispatch(cmd_design)
    assert res_design.status == "success"
    assert res_design.action == "story_handled"

    # 2. QA Review
    cmd_qa_review = ExecutionCommand(
        execution_id="exec_qa_rev_1",
        work_type=WorkType.QA_REVIEW,
        entity_type="task",
        entity_id=401,
        context={"work_type": "qa_review"},
    )
    res_qa_rev = coord.dispatch(cmd_qa_review)
    assert res_qa_rev.status == "success"
    assert res_qa_rev.action == "approve"


def test_routed_invoker_with_work_type_priority():
    """Verify RoutedSubprocessInvoker routes by work_type when provided."""
    from agentboard.processors.invokers import RoutedSubprocessInvoker

    inv = RoutedSubprocessInvoker(
        commands={
            "designer": "echo designer",
            "coder": "echo coder",
            "tester": "echo tester",
        },
        routing={
            "design": "designer",
            "implementation": "coder",
            "qa": "tester",
        },
        timeout=5,
    )

    alias, _ = inv.route("design")
    assert alias == "designer"

    alias, _ = inv.route("qa")
    assert alias == "tester"

    alias, _ = inv.route("implementation")
    assert alias == "coder"


# -------------------------------------------------------------
# P2 修复：build_prompt_for 优先按 work_type 路由而非 action 字符串
# -------------------------------------------------------------
class _RoutingSpyHandler:
    """记录 build_prompt 被调用时收到的 context 副本，便于断言路由命中。"""

    def __init__(self, name: str):
        self.name = name
        self.last_ctx: dict | None = None

    def can_handle(self, work_item):  # 兼容 handlers 接口
        return False

    def fetch(self):  # poll_once 跳过
        return []

    def build_prompt(self, context):
        self.last_ctx = dict(context)
        return f"[{self.name}] prompt for {context.get('work_type', '?')}"

    # Coordinator.dispatch 调 handler.execute_command
    def execute_command(self, command, invoker):
        return ExecutionResult.success(
            command.execution_id, action="noop", summary=f"handled by {self.name}",
        )


def test_build_prompt_for_routes_granular_review_work_types():
    """P2：build_prompt_for 必须按 work_type 字符串精确路由到对应 handler。

    修复前：所有 *_REVIEW 都被 action="review_task" 抹平成 TASK_REVIEW。
    修复后：work_type="design_review" 直接命中 DESIGN_REVIEW key。
    """
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()

    coord = ProcessorCoordinator(config, invoker=CallableProcessorInvoker(lambda c: AgentDecision(action="noop")), client=dummy)

    # 注入 spy handler（覆盖默认 registry）
    design_review_spy = _RoutingSpyHandler("design_review")
    impl_review_spy = _RoutingSpyHandler("implementation_review")
    qa_review_spy = _RoutingSpyHandler("qa_review")
    legacy_review_spy = _RoutingSpyHandler("task_review_legacy")

    coord.registry = {
        WorkType.DESIGN_REVIEW: design_review_spy,
        WorkType.IMPLEMENTATION_REVIEW: impl_review_spy,
        WorkType.QA_REVIEW: qa_review_spy,
        WorkType.TASK_REVIEW: legacy_review_spy,  # legacy key 不应被命中
        WorkType.PROPOSAL_CLARIFY: _RoutingSpyHandler("clarify"),
    }

    # 1. design_review 必须命中 DESIGN_REVIEW
    p = coord.build_prompt_for({"work_type": "design_review", "task_id": 1})
    assert p == "[design_review] prompt for design_review"
    assert design_review_spy.last_ctx["work_type"] == "design_review"
    assert legacy_review_spy.last_ctx is None  # 关键：legacy 不应被命中

    # 2. implementation_review 命中自己的 handler
    p = coord.build_prompt_for({"work_type": "implementation_review", "task_id": 2})
    assert p == "[implementation_review] prompt for implementation_review"
    assert impl_review_spy.last_ctx is not None
    assert legacy_review_spy.last_ctx is None

    # 3. qa_review 命中自己的 handler
    p = coord.build_prompt_for({"work_type": "qa_review", "task_id": 3})
    assert p == "[qa_review] prompt for qa_review"
    assert qa_review_spy.last_ctx is not None
    assert legacy_review_spy.last_ctx is None


def test_build_prompt_for_falls_back_to_action_when_work_type_missing():
    """兼容：work_type 缺失时仍能按 action 字符串路由（向后兼容旧 invoker / MQ 消息）。"""
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()
    coord = ProcessorCoordinator(config, invoker=CallableProcessorInvoker(lambda c: AgentDecision(action="noop")), client=dummy)

    legacy_review_spy = _RoutingSpyHandler("task_review_legacy")
    coord.registry = {
        WorkType.TASK_REVIEW: legacy_review_spy,
        WorkType.PROPOSAL_CLARIFY: _RoutingSpyHandler("clarify"),
    }

    # 没 work_type，但有 action="review_task" → 走 legacy 兼容路径
    p = coord.build_prompt_for({"action": "review_task", "task_id": 1})
    assert "[task_review_legacy]" in p
    assert legacy_review_spy.last_ctx is not None


def test_build_prompt_for_handles_work_type_enum_value():
    """work_type 传 WorkType 枚举值（非字符串）也能正确路由。"""
    config = ProcessorConfig(agent="test_agent", agent_cmd="echo test")
    dummy = DummyClient()
    coord = ProcessorCoordinator(config, invoker=CallableProcessorInvoker(lambda c: AgentDecision(action="noop")), client=dummy)

    design_review_spy = _RoutingSpyHandler("design_review")
    coord.registry = {
        WorkType.DESIGN_REVIEW: design_review_spy,
        WorkType.PROPOSAL_CLARIFY: _RoutingSpyHandler("clarify"),
    }

    p = coord.build_prompt_for({"work_type": WorkType.DESIGN_REVIEW, "task_id": 1})
    assert "[design_review]" in p
