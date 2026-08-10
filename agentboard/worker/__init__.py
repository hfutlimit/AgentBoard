"""AgentBoard Proposal Worker 包（Epic 123 Step 2 · Worker 拆 Handler 类）。

原 ``agentboard/worker.py``（1808 行）拆分为本包，模块结构：:

    agentboard/worker/
    ├── __init__.py          # 本文件：re-export 全部公开符号（向后兼容）
    ├── worker.py            # 主循环：发现路由 + 维护编排 + MQ/心跳 + CLI
    ├── config.py            # WorkerConfig / AgentDecision / 异常 / 协议
    ├── invokers.py          # SubprocessAgentInvoker / CallableAgentInvoker
    ├── maintenance.py       # reclaim_stale / recover_failed / sweep
    └── handlers/
        ├── base.py          # Handler 协议
        ├── clarify.py       # ClarifyHandler（需求澄清）
        ├── ticket.py        # TicketHandler（Proposal→Ticket 转化）
        └── story.py         # StoryHandler（Story 编排 + Task 竞争/定向）

向后兼容：``from agentboard.worker import ProposalWorker, WorkerConfig, ...``
与旧模块用法完全一致；``python -m agentboard.worker`` 走 ``__main__.py``。
"""
from __future__ import annotations

from .config import (
    ACTION_ASK,
    ACTION_FAIL,
    ACTION_FINALIZE,
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
from .invokers import (
    CallableAgentInvoker,
    SubprocessAgentInvoker,
    build_prompt,
    extract_decision_json,
    set_prompt_builder,
    split_command,
)
# 旧模块级 prompt 构建函数兼容（原 worker.py 顶层 _build_story_prompt 等）
from .handlers.story import build_story_prompt as _build_story_prompt  # noqa: E402
from .handlers.story import build_task_prompt as _build_task_prompt  # noqa: E402
from .handlers.ticket import build_ticket_prompt as _build_ticket_prompt  # noqa: E402
from .cli import main
from .worker import ProposalWorker, _parse_dt

__all__ = [
    # 常量
    "ACTION_ASK", "ACTION_FAIL", "ACTION_FINALIZE", "ACTION_STORY_HANDLED",
    "ACTION_TICKET_CREATED", "CLAIMABLE_STATUSES", "VALID_ACTIONS",
    # 异常
    "AgentInvocationError", "AgentOutputError", "WorkerError",
    # 数据 / 配置
    "AgentDecision", "AgentInvoker", "WorkerConfig",
    # 调用器
    "CallableAgentInvoker", "SubprocessAgentInvoker",
    "build_prompt", "extract_decision_json", "split_command", "set_prompt_builder",
    # prompt 构建（旧私有名兼容）
    "_build_story_prompt", "_build_task_prompt", "_build_ticket_prompt",
    # 工具
    "_parse_dt",
    # 主体
    "ProposalWorker", "main",
]
