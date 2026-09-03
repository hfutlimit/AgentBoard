"""AgentBoard Processor runtime (Unified Execution Model).

P7b (2026-09-03): the runtime used to live in a differently-named package
with two compat facades in front of it, and its classes carried the legacy
"Proposal Worker" wording. Everything now lives here under the Processor
naming: the proposal main loop, the runtime configuration, the unified
coordinator and the headless-agent invocation protocol. There is no compat
shim - the legacy module paths and class names are gone.

The unified runtime exposes:
- :class:`ProcessorCoordinator` - unified single-process coordinator across all WorkTypes
- :class:`WorkType` - unified execution type enum
- :class:`ExecutionCommand` / :class:`ExecutionResult` - unified execution contracts
- :class:`ProposalProcessor` - main loop class
- :class:`ProcessorConfig` - runtime configuration
- :class:`AgentDecision` / :class:`ProcessorInvoker` - Agent protocol types
"""
from __future__ import annotations
from .config import (  # noqa: F401
    ACTION_ASK,
    ACTION_FAIL,
    ACTION_FINALIZE,
    ACTION_REVIEW_APPROVE,
    ACTION_REVIEW_REJECT,
    ACTION_STORY_HANDLED,
    ACTION_TICKET_CREATED,
    CLAIMABLE_STATUSES,
    VALID_ACTIONS,
    AgentDecision,
    AgentInvocationError,
    AgentOutputError,
    PermanentAgentError,
    ProcessorConfig,
    ProcessorInvoker,
    TransientAgentError,
    WorkerError,
)
from .contract import (  # noqa: F401
    ExecutionAction,
    ExecutionCommand,
    ExecutionResult,
    ExecutionStatus,
    UnknownWorkTypeError,
    WorkType,
)
from .coordinator import ProcessorCoordinator  # noqa: F401
from .invokers import (  # noqa: F401
    CallableProcessorInvoker,
    ComplianceEnforcingInvoker,
    RoutedSubprocessInvoker,
    SubprocessProcessorInvoker,
    build_prompt,
    extract_decision_json,
    parse_agent_command_map,
    parse_agent_routing,
    set_prompt_builder,
    split_command,
)
from .handlers.story import (  # noqa: E402, F401
    build_story_prompt as _build_story_prompt,
    build_task_prompt as _build_task_prompt,
)
from .handlers.ticket import build_ticket_prompt as _build_ticket_prompt  # noqa: E402, F401
from .cli import main  # noqa: F401
from .worker import ProposalProcessor, _parse_dt  # noqa: F401

__all__ = [
    # 统一模型
    "ProcessorCoordinator", "WorkType", "ExecutionCommand", "ExecutionResult",
    "ExecutionAction", "ExecutionStatus", "UnknownWorkTypeError",
    # 常量
    "ACTION_ASK", "ACTION_FAIL", "ACTION_FINALIZE", "ACTION_REVIEW_APPROVE",
    "ACTION_REVIEW_REJECT", "ACTION_STORY_HANDLED",
    "ACTION_TICKET_CREATED", "CLAIMABLE_STATUSES", "VALID_ACTIONS",
    # 异常
    "AgentInvocationError", "TransientAgentError", "PermanentAgentError",
    "AgentOutputError", "WorkerError",
    # 数据 / 配置
    "AgentDecision", "ProcessorInvoker", "ProcessorConfig",
    # 调用器
    "CallableProcessorInvoker", "ComplianceEnforcingInvoker",
    "RoutedSubprocessInvoker", "SubprocessProcessorInvoker",
    "build_prompt", "extract_decision_json", "split_command", "set_prompt_builder",
    "parse_agent_command_map", "parse_agent_routing",
    # prompt 构建（旧私有名兼容）
    "_build_story_prompt", "_build_task_prompt", "_build_ticket_prompt",
    # 工具
    "_parse_dt",
    # 主体
    "ProposalProcessor", "main",
]
