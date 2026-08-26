"""统一工作项与执行契约（Unified Execution Contract）。

语言中立契约：定义 Worker 与 Server 之间传递的执行命令、工作类型与结构化结果。
解耦业务领域模型（Proposal / Epic / Story / Task 保持独立表与领域逻辑）与底层执行管道。
"""
from __future__ import annotations

from enum import Enum, StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class WorkType(str, Enum):
    """统一执行工作类型枚举（涵盖 Proposal 全生命周期与 Task 正交业务类型）。"""

    # Proposal 生命周期
    PROPOSAL_CLARIFY = "proposal_clarify"
    PROPOSAL_CONVERT = "proposal_convert"

    # Task 一等公民正交业务执行类型
    DESIGN = "design"
    DESIGN_REVIEW = "design_review"
    IMPLEMENTATION = "implementation"
    IMPLEMENTATION_REVIEW = "implementation_review"
    QA = "qa"
    QA_REVIEW = "qa_review"

    # 向后兼容别名（仅 parser / adapter 接受，runtime 内部必须立即 normalize 成 canonical）
    TASK_IMPLEMENT = "task_implement"
    TASK_REVIEW = "task_review"
    TASK_RESPOND = "task_respond"


    @classmethod
    def from_task(cls, task_type: str | None, is_review: bool = False) -> WorkType:
        """根据 Task 的业务类型（design / dev / qa 等）与阶段推导对应的 WorkType。"""
        t = (task_type or "implementation").lower().strip()
        if is_review:
            if t == "design":
                return cls.DESIGN_REVIEW
            elif t in ("qa", "test", "testing"):
                return cls.QA_REVIEW
            return cls.IMPLEMENTATION_REVIEW
        else:
            if t == "design":
                return cls.DESIGN
            elif t in ("qa", "test", "testing"):
                return cls.QA
            return cls.IMPLEMENTATION

    @classmethod
    def canonical_for(cls, work_type: "WorkType | str | None") -> "WorkType":
        """把 legacy alias 归一化成 canonical WorkType。

        Review 2026-08-26：TASK_IMPLEMENT / TASK_REVIEW 是历史兼容别名，
        内部 runtime 一旦进入统一模型必须立即 normalize 成 canonical，
        避免后续 prompt / context / behavior 计算走"平级兼容分支"。

        映射：
            TASK_IMPLEMENT → IMPLEMENTATION
            TASK_REVIEW    → IMPLEMENTATION_REVIEW（保守默认；design/qa 区分已由
                              业务侧 work_type 字段承载）
            TASK_RESPOND   → 保留（owner response 是独立 WorkType）
            其他           → 不变
        """
        if work_type is None:
            raise UnknownWorkTypeError("work_type is required")
        if isinstance(work_type, cls):
            wt = work_type
        else:
            try:
                wt = cls(str(work_type).strip().lower())
            except (ValueError, KeyError) as exc:
                raise UnknownWorkTypeError(
                    f"unknown work_type: {work_type!r}"
                ) from exc
        if wt == cls.TASK_IMPLEMENT:
            return cls.IMPLEMENTATION
        if wt == cls.TASK_REVIEW:
            return cls.IMPLEMENTATION_REVIEW
        return wt


class UnknownWorkTypeError(ValueError):
    """Raised when an execution request contains an unknown work type."""


class ExecutionCommand(BaseModel):
    """Worker 接收到的统一执行命令。"""

    execution_id: str = Field(..., description="执行唯一标识，通常对应 AgentRun.id 或 (type, id, attempt)")
    work_type: WorkType = Field(..., description="执行工作类型")
    entity_type: str = Field(..., description="所属业务领域实体类型：proposal | task | story")
    entity_id: int = Field(..., description="所属业务领域实体 ID")
    attempt: int = Field(default=1, description="当前尝试轮次")
    context: dict[str, Any] = Field(default_factory=dict, description="执行上下文（项目路径、历史记录、依赖等）")
    lease_token: str | None = Field(default=None, description="租约 token，用于心跳续租与 CAS 校验")


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"


class ExecutionAction(StrEnum):
    ASK = "ask"
    FINALIZE = "finalize"
    FAIL = "fail"
    TICKET_CREATED = "ticket_created"
    STORY_HANDLED = "story_handled"
    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"
    SKIPPED = "skipped"
    NOOP = "noop"


class ExecutionResult(BaseModel):
    """Worker 执行完毕后上报的结构化结果。"""

    execution_id: str = Field(..., description="对应的执行标识")
    status: ExecutionStatus = Field(..., description="Execution status")
    action: ExecutionAction = Field(..., description="Execution action")
    summary: str = Field(default="", description="执行摘要或评论内容")
    output: dict[str, Any] = Field(default_factory=dict, description="额外结构化输出（如生成的 tasks, questions 等）")
    inspected_files: list[str] = Field(default_factory=list, description="Agent 报告已读/检查过的源码文件列表")
    error_message: str | None = Field(default=None, description="失败时的错误信息")

    @classmethod
    def success(
        cls,
        execution_id: str,
        action: str,
        summary: str = "",
        output: dict[str, Any] | None = None,
        inspected_files: list[str] | None = None,
    ) -> ExecutionResult:
        return cls(
            execution_id=execution_id,
            status="success",
            action=action,
            summary=summary,
            output=output or {},
            inspected_files=inspected_files or [],
        )

    @classmethod
    def skipped(
        cls,
        execution_id: str,
        summary: str = "",
        *,
        action: str = "skipped",
    ) -> "ExecutionResult":
        return cls(
            execution_id=execution_id,
            status=ExecutionStatus.SKIPPED,
            action=ExecutionAction(action),
            summary=summary,
        )

    @classmethod
    def failure(
        cls,
        execution_id: str,
        error: str,
        action: str = "fail",
        summary: str = "",
        inspected_files: list[str] | None = None,
    ) -> ExecutionResult:
        return cls(
            execution_id=execution_id,
            status="failed",
            action=action,
            summary=summary or error,
            error_message=error,
            inspected_files=inspected_files or [],
        )


# -----------------------------------------------------------------------------
# PreparedExecution：dispatch 前的不可变执行包
# -----------------------------------------------------------------------------
# Review 2026-08-26 P1 修复：原 Worker 主流程从
#
#   Handler.load_context() → invoker.invoke(context) → 全局 _prompt_builder
#
#   没有调用 BehaviorResolver / ContextBuilder / PromptBuilder。新模块
#   都是 dead code。本结构把"behavior 解析 + context 装配 + prompt 渲染"
#   集中在 dispatch 入口前完成，作为不可变包传给 handler 协议。
#
# 设计原则：
# 1. 不可变（frozen model）—— handler 收到后不应再改；
# 2. 包含最终 prompt —— handler / invoker 拿到即可用，无需再查 _prompt_builder；
# 3. work_type 已是 canonical（legacy alias 已被 normalize）—— 业务层不再判断 alias。
# 4. backward-compat：handler 仍可走旧协议 execute_command(command, invoker)；
#   PreparedExecution 仅作为 opt-in 路径。

class PreparedExecution(BaseModel):
    """dispatch 前完成的不可变执行包。

    字段：
    - command: 原始 ExecutionCommand（已 normalize work_type）
    - work_type: canonical WorkType（与 command.work_type 一致；提供便利访问）
    - behavior: 解析后的 EffectiveBehaviorConfig
    - execution_context: 装配后的 ExecutionContext
    - prompt: 最终给 Agent 的 prompt
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    command: ExecutionCommand
    work_type: WorkType
    behavior: Any  # EffectiveBehaviorConfig（避免循环 import；运行时真实类型可被 isinstance 校验）
    execution_context: Any  # ExecutionContext（同上）
    prompt: str
    # 准备阶段耗时（毫秒），便于 metrics / log
    prepare_ms: int = 0

