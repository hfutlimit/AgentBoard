"""AgentBoard Worker runtime (Unified Execution Model).

The implementation lives in ``agentboard.agent_runtime``.
The unified runtime exposes:
- :class:`WorkerCoordinator` — unified single-process coordinator across all WorkTypes
- :class:`WorkType` — unified execution type enum
- :class:`ExecutionCommand` / :class:`ExecutionResult` — unified execution contracts
- :class:`ProposalWorker` — backward-compatible main loop class
- :class:`WorkerConfig` — runtime configuration
- :class:`AgentDecision` / :class:`AgentInvoker` — Agent protocol types
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
    AgentInvoker,
    AgentOutputError,
    PermanentAgentError,
    TransientAgentError,
    WorkerConfig,
    WorkerError,
)
from .contract import (  # noqa: F401
    ExecutionCommand,
    ExecutionResult,
    ExecutionAction,
    ExecutionStatus,
    UnknownWorkTypeError,
    WorkType,
)
from .coordinator import WorkerCoordinator  # noqa: F401
from .invokers import (  # noqa: F401
    CallableAgentInvoker,
    RoutedSubprocessInvoker,
    SubprocessAgentInvoker,
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
from .worker import ProposalWorker, _parse_dt  # noqa: F401

__all__ = [
    # 统一模型
    "WorkerCoordinator", "WorkType", "ExecutionCommand", "ExecutionResult",
    "ExecutionAction", "ExecutionStatus", "UnknownWorkTypeError",
    # 常量
    "ACTION_ASK", "ACTION_FAIL", "ACTION_FINALIZE", "ACTION_REVIEW_APPROVE",
    "ACTION_REVIEW_REJECT", "ACTION_STORY_HANDLED",
    "ACTION_TICKET_CREATED", "CLAIMABLE_STATUSES", "VALID_ACTIONS",
    # 异常
    "AgentInvocationError", "TransientAgentError", "PermanentAgentError",
    "AgentOutputError", "WorkerError",
    # 数据 / 配置
    "AgentDecision", "AgentInvoker", "WorkerConfig",
    # 调用器
    "CallableAgentInvoker", "RoutedSubprocessInvoker", "SubprocessAgentInvoker",
    "build_prompt", "extract_decision_json", "split_command", "set_prompt_builder",
    "parse_agent_command_map", "parse_agent_routing",
    # prompt 构建（旧私有名兼容）
    "_build_story_prompt", "_build_task_prompt", "_build_ticket_prompt",
    # 工具
    "_parse_dt",
    # 主体
    "ProposalWorker", "main",
]
