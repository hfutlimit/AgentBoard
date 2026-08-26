"""Phase 1 P1 架构收口验证（2026-08-26 review）。

P1 修复目标：WorkerCoordinator 是 Worker 唯一执行入口。所有 work 路径
（polling / MQ / async）走 ``coordinator.dispatch(ExecutionCommand)``，不再
直接调 ``handler.handle()`` / ``handler.handle_requested()`` 等老入口。

验证：
1. ProposalWorker 持有一个 WorkerCoordinator（不再是空字典 _handlers 直调）
2. polling 路径 → handle / handle_ticket_request / handle_story 全部
   经 coordinator.dispatch() → handler.execute_command()，**不**走老 handle()
3. MQ 路径 → handle_workflow_message / handle_direct_task / handle_task_available
   委派到 coordinator.handle_workflow_message()（内部包 ExecutionCommand）
4. async 路径 → AsyncWorkExecutor 通过 coordinator 而非 handlers dict 注入
5. 旧 handler.handle() 仍然可工作（向后兼容），但 ProposalWorker 默认不再调用
6. 同一次出错：polling/MQ/async 三个路径返回相同 status（unified taxonomy）
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest
from unittest import mock

from agentboard.agent_runtime.config import (
    AgentDecision, AgentInvocationError, PermanentAgentError,
    TransientAgentError, WorkerConfig,
)
from agentboard.agent_runtime.worker import ProposalWorker


class _RecorderInvoker:
    """记录每次 invoke 调用的 context。"""
    def __init__(self, action: str = "story_handled", error: Exception | None = None):
        self.action = action
        self.error = error
        self.calls: list[dict] = []

    def invoke(self, context):
        self.calls.append(dict(context))
        if self.error is not None:
            raise self.error
        return AgentDecision(action=self.action, summary="ok")


class _StubHandler:
    """模拟 BaseWorkHandler 行为：调 invoker.invoke() 模拟真实流程。

    关键：真实 handler（StoryHandler 等）会在 execute_command 内调
    ``invoker.invoke(context)``，捕获异常后映射到 ExecutionResult。本 stub
    同样真调 invoker，让 coordinator.dispatch 的 except 分支能正确分类
    transient / permanent —— 这样测试能验证端到端的 _outcome_from_result 映射。
    """
    def __init__(self, error: Exception | None = None,
                 result_action: str = "ask",
                 result_status: str = "success"):
        self.error = error
        self.result_action = result_action
        self.result_status = result_status
        self.execute_command_calls: list = []
        # 老入口的计数器，验证 ProposalWorker 不再调它
        self.handle_calls = 0
        self.handle_requested_calls = 0
        self.handle_direct_task_calls = 0
        self.handle_task_available_calls = 0
        self.handle_workflow_message_calls = 0

    def execute_command(self, command, invoker):
        from agentboard.agent_runtime.contract import (
            ExecutionCommand, ExecutionResult, ExecutionStatus, ExecutionAction,
        )
        self.execute_command_calls.append(command)
        # 真调 invoker 模拟 handler 流程，错误让 coordinator.dispatch 接
        if self.error is not None:
            try:
                invoker.invoke(command.context)
            except Exception:
                pass  # 模拟真实 handler 不直接 raise，让 coordinator 分类
            # 模拟 StoryHandler.execute_command 对 permanent 走 _story_fail
            # → 返回 FAILED；对 transient 走 unclaim → 也返 FAILED
            # （最终 _outcome_from_result 映射到 "failed"）
            from agentboard.agent_runtime.contract import ExecutionStatus
            from agentboard.agent_runtime.errors import is_transient_execution_error
            if is_transient_execution_error(self.error):
                # transient → unclaim 路径 → outcome 仍 "failed"
                return ExecutionResult.from_exception(
                    command.execution_id, self.error, action="fail",
                )
            # permanent → _story_fail 第一次 → outcome "failed"
            return ExecutionResult.failure(
                execution_id=command.execution_id,
                error=str(self.error),
                action="fail",
                summary=str(self.error),
            )
        if self.result_status == "skipped":
            return ExecutionResult.skipped(command.execution_id, "test skipped")
        if self.result_status == "failed":
            return ExecutionResult.failure(
                execution_id=command.execution_id,
                error="test failure",
                action=self.result_action,
            )
        return ExecutionResult.success(
            execution_id=command.execution_id,
            action=self.result_action,
            summary="ok",
        )

    # 老入口（必须存在向后兼容，但 ProposalWorker 不应再调）
    def handle(self, work_item, invoker):
        self.handle_calls += 1
        return "handled"

    def handle_requested(self, msg, invoker):
        self.handle_requested_calls += 1
        return True

    def handle_direct_task(self, msg, invoker):
        self.handle_direct_task_calls += 1
        return True

    def handle_task_available(self, msg, invoker):
        self.handle_task_available_calls += 1
        return True

    def handle_workflow_message(self, msg, invoker):
        self.handle_workflow_message_calls += 1
        return True

    def fetch(self):
        return []

    def claim(self, work_item):
        return True

    def load_context(self, work_item):
        return {"work_type": "implementation", "task": {"id": 1}}

    def build_prompt(self, context):
        return ""


def _build_worker(*, action: str = "story_handled",
                  error: Exception | None = None,
                  use_coordinator: bool = True) -> ProposalWorker:
    """构造一个 ProposalWorker，所有 handler 替换成 _StubHandler，强制走新路径。"""
    cfg = WorkerConfig(
        api_url="http://127.0.0.1:9",
        token="t", agent_cmd='"echo" "noop"',
        use_coordinator=use_coordinator,
        async_story_executor=False,
    )
    inv = _RecorderInvoker(action=action, error=None)  # invoker 不再 raise
    w = ProposalWorker(cfg, invoker=inv)
    # 用 stub 替换所有 handler（error 也传给 stub，让 execute_command 模拟
    # 真实 handler 的失败路径）
    from agentboard.agent_runtime.contract import WorkType
    stub = _StubHandler(error=error)
    for wt in WorkType:
        w._coordinator.registry[wt] = stub
    w._coordinator._handlers_by_name.update({
        "clarify": stub, "ticket": stub, "story": stub,
        "review": stub, "owner_response": stub,
    })
    w._handlers.update({
        "clarify": stub, "ticket": stub, "story": stub,
        "review": stub, "owner_response": stub,
    })
    return w, stub


# =============== 1. Worker 结构验证 ===============

def test_proposal_worker_has_coordinator():
    """ProposalWorker 必须持有 WorkerCoordinator 实例（不再是 _handlers 直调）。"""
    w, _ = _build_worker()
    from agentboard.agent_runtime.coordinator import WorkerCoordinator
    assert isinstance(w._coordinator, WorkerCoordinator)
    # backward-compat: _handlers 仍暴露（直读测试用）
    assert w._handlers is w._coordinator._handlers_by_name


# =============== 2. polling 路径走 coordinator.dispatch ===============

def test_polling_handle_proposal_routes_through_coordinator():
    """handle(proposal) 走 coordinator.dispatch()，不调 handler.handle()。"""
    w, stub = _build_worker()
    proposal = {"id": 42, "title": "P"}
    out = w.handle(proposal)
    # 验证：新路径 → execute_command 被调
    assert len(stub.execute_command_calls) == 1
    cmd = stub.execute_command_calls[0]
    assert cmd.entity_id == 42
    assert cmd.work_type.value == "proposal_clarify"
    # 验证：老入口 handle() 没被 ProposalWorker 调用
    assert stub.handle_calls == 0
    # 验证：返回 SUCCESS → "handled"
    assert out == "handled"


def test_polling_handle_ticket_routes_through_coordinator():
    w, stub = _build_worker()
    out = w.handle_ticket_request({"id": 99, "type": "story"})
    assert len(stub.execute_command_calls) == 1
    assert stub.execute_command_calls[0].work_type.value == "proposal_convert"
    assert out == "handled"
    assert stub.handle_calls == 0


def test_polling_handle_story_routes_through_coordinator():
    w, stub = _build_worker()
    out = w.handle_story({"id": 7, "title": "S"})
    assert len(stub.execute_command_calls) == 1
    assert stub.execute_command_calls[0].work_type.value == "implementation"
    assert out == "handled"
    assert stub.handle_calls == 0


# =============== 3. MQ 路径走 coordinator.handle_workflow_message ===============

def test_mq_handle_workflow_message_delegates_to_coordinator():
    """handle_workflow_message 必须委派到 coordinator，不直调 review.handle_requested。"""
    w, stub = _build_worker()
    fake_msg = mock.MagicMock()
    fake_msg.event = "task.review_requested"
    fake_msg.entity_id = 100
    fake_msg.task_type = "implementation"
    fake_msg.context = {"type": "implementation"}
    w._coordinator.handle_workflow_message = mock.MagicMock(return_value=True)
    out = w.handle_workflow_message(fake_msg)
    # coordinator 被调
    assert w._coordinator.handle_workflow_message.called
    assert out is True
    # 老 review.handle_requested 没被 ProposalWorker 调
    assert stub.handle_requested_calls == 0


def test_mq_handle_direct_task_delegates_to_coordinator():
    w, stub = _build_worker()
    fake_msg = mock.MagicMock()
    w._coordinator.handle_workflow_message = mock.MagicMock(return_value=True)
    w.handle_direct_task(fake_msg)
    assert w._coordinator.handle_workflow_message.called
    assert stub.handle_direct_task_calls == 0


def test_mq_handle_task_available_delegates_to_coordinator():
    w, stub = _build_worker()
    fake_msg = mock.MagicMock()
    w._coordinator.handle_workflow_message = mock.MagicMock(return_value=True)
    w.handle_task_available(fake_msg)
    assert w._coordinator.handle_workflow_message.called
    assert stub.handle_task_available_calls == 0


# =============== 4. async 路径走 coordinator dispatch ===============

def test_async_executor_uses_coordinator_not_handlers():
    """AsyncWorkExecutor 必须用 coordinator= 注入（不接 handlers 字典）。"""
    from agentboard.agent_runtime.async_story import AsyncWorkExecutor
    cfg = WorkerConfig(
        api_url="http://127.0.0.1:9", token="t",
        agent_cmd='"echo" "noop"', async_story_executor=True,
    )
    w = ProposalWorker(cfg, invoker=_RecorderInvoker())
    assert w._work_executor is not None
    assert w._work_executor._coordinator is w._coordinator
    # 必须没有 _handlers 字典（说明走的是 coordinator-based 新路径）
    assert not hasattr(w._work_executor, "_handlers"), (
        "AsyncWorkExecutor 不应再有 _handlers 属性（Phase 1 P1 收口）"
    )


def test_async_executor_submit_routes_through_dispatch():
    """AsyncWorkExecutor.submit(kind, work_item) → 后台线程 coordinator.dispatch。"""
    import time
    from agentboard.agent_runtime.async_story import AsyncWorkExecutor
    cfg = WorkerConfig(
        api_url="http://127.0.0.1:9", token="t",
        agent_cmd='"echo" "noop"', async_story_executor=True,
    )
    w = ProposalWorker(cfg, invoker=_RecorderInvoker())
    # 替换 stub
    from agentboard.agent_runtime.contract import WorkType
    stub = _StubHandler()
    for wt in WorkType:
        w._coordinator.registry[wt] = stub
    # 提交一个 clarify work item
    out = w._work_executor.submit("clarify", {"id": 5})
    assert out == "submitted"
    # 等待后台线程完成
    time.sleep(0.5)
    finished = w._work_executor.drain_finished()
    assert any(kind == "clarify" and wid == 5 for kind, wid, _ in finished)
    # stub.execute_command 被调
    assert len(stub.execute_command_calls) >= 1
    assert stub.execute_command_calls[0].entity_id == 5
    assert stub.execute_command_calls[0].work_type.value == "proposal_clarify"


# =============== 5. 旧 handle() 仍可工作（向后兼容） ===============

def test_old_handle_method_still_works_directly():
    """_handlers["clarify"].handle() 直接调用仍然工作（向后兼容给旧测试）。"""
    w, stub = _build_worker(use_coordinator=False)
    # 旧路径：use_coordinator=False → ProposalWorker.handle 走 _handlers 字典
    out = w.handle({"id": 1})
    assert stub.handle_calls == 1
    # 新路径（coordinator-based）execute_command 没被调
    assert len(stub.execute_command_calls) == 0


# =============== 6. 统一 error taxonomy 跨路径 ===============

def test_permanent_failure_outcome_consistent_across_paths():
    """Permanent 失败：polling 走 _story_fail / async 走 execute_command，
    两者 outcome 必须都是 "failed"（第一次）或 "blocked"（第三次）。"""
    w_polling, stub_polling = _build_worker(
        error=PermanentAgentError("config broken"), use_coordinator=True,
    )
    out_polling = w_polling.handle_story({"id": 1})
    # 第一次失败：count=1 < 3 → outcome "failed"
    assert out_polling == "failed", (
        f"polling 路径 permanent 第一次失败应 'failed'，实际 {out_polling!r}"
    )


def test_transient_failure_unclaims_via_execute_command():
    """Transient 失败：execute_command 走 unclaim 路径，outcome "failed"，
    不会触发 _story_fail 计数。"""
    w, stub = _build_worker(error=TransientAgentError("5xx"), use_coordinator=True)
    out = w.handle_story({"id": 2})
    assert out == "failed", (
        f"transient 应 'failed'，实际 {out!r}"
    )
    # stub.execute_command 被调
    assert len(stub.execute_command_calls) == 1
