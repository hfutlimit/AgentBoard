"""AgentBoard Proposal Worker runtime.

The implementation lives in ``agentboard.agent_runtime``.  The historical
``agentboard.worker`` and ``agentboard.features.workers`` paths are kept as
compatibility entry points.

The worker consumes work items off the AgentBoard MQ, runs an Agent (CLI
subprocess or in-process callable) to act on them, and reports results back
to the REST API.

Public entry points (re-exported for backward compat with the old
``agentboard.worker`` module):

- :class:`ProposalWorker` — main loop class
- :class:`WorkerConfig` — runtime configuration
- :class:`AgentDecision` / :class:`AgentInvoker` — Agent protocol types
- :class:`CallableAgentInvoker` / :class:`SubprocessAgentInvoker` — built-in
  Agent runners
- :func:`main` — CLI entry point (``python -m agentboard.worker`` /
  ``python -m agentboard.features.workers``)

Internal modules:

- :mod:`.config` — WorkerConfig / AgentDecision / 异常 / 协议
- :mod:`.invokers` — SubprocessAgentInvoker / CallableAgentInvoker
- :mod:`.maintenance` — reclaim_stale / recover_failed / sweep
- :mod:`.heartbeat` — agent heartbeat thread
- :mod:`.cli` — argument parser + main()
- :mod:`.worker` — main loop
- :mod:`.handlers` — Handler protocol + concrete handlers
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
    WorkerConfig,
    WorkerError,
)
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
# 旧模块级 prompt 构建函数兼容（原 worker.py 顶层 _build_story_prompt 等）
from .handlers.story import (  # noqa: E402, F401
    build_story_prompt as _build_story_prompt,
    build_task_prompt as _build_task_prompt,
)
from .handlers.ticket import build_ticket_prompt as _build_ticket_prompt  # noqa: E402, F401
from .cli import main  # noqa: F401
from .worker import ProposalWorker, _parse_dt  # noqa: F401

__all__ = [
    # 常量
    "ACTION_ASK", "ACTION_FAIL", "ACTION_FINALIZE", "ACTION_REVIEW_APPROVE",
    "ACTION_REVIEW_REJECT", "ACTION_STORY_HANDLED",
    "ACTION_TICKET_CREATED", "CLAIMABLE_STATUSES", "VALID_ACTIONS",
    # 异常
    "AgentInvocationError", "AgentOutputError", "WorkerError",
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
