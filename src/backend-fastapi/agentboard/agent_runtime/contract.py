"""统一工作项与执行契约（Unified Execution Contract）。

语言中立契约：定义 Worker 与 Server 之间传递的执行命令、工作类型与结构化结果。
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class WorkType(str, Enum):
    """统一执行工作类型枚举。"""

    #: 需求澄清与问答（对应 Proposal analyzing / awaiting_user）
    PROPOSAL_CLARIFY = "proposal_clarify"
    #: 提案转换为 Story & Task DAG（对应 Proposal converting / ticket_request）
    PROPOSAL_CONVERT = "proposal_convert"
    #: 任务执行实现（涵盖 design, dev, qa, bug，由 Task.type 区分上下文）
    TASK_IMPLEMENT = "task_implement"
    #: 独立 Reviewer 审查与投票（approve / reject）
    TASK_REVIEW = "task_review"
    #: Owner 针对 Review 驳回意见的修复与回应（Rework / Follow-up）
    TASK_RESPOND = "task_respond"


class ExecutionCommand(BaseModel):
    """Worker 接收到的统一执行命令。"""

    execution_id: str = Field(..., description="执行唯一标识，通常对应 AgentRun.id 或 (type, id, attempt)")
    work_type: WorkType = Field(..., description="执行工作类型")
    entity_type: str = Field(..., description="所属业务领域实体类型：proposal | task | story")
    entity_id: int = Field(..., description="所属业务领域实体 ID")
    attempt: int = Field(default=1, description="当前尝试轮次")
    context: dict[str, Any] = Field(default_factory=dict, description="执行上下文（项目路径、历史记录、依赖等）")
    lease_token: str | None = Field(default=None, description="租约 token，用于心跳续租与 CAS 校验")


class ExecutionResult(BaseModel):
    """Worker 执行完毕后上报的结构化结果。"""

    execution_id: str = Field(..., description="对应的执行标识")
    status: str = Field(..., description="执行状态：success | failed | rejected | blocked")
    action: str = Field(..., description="决策动作码：ask | create_ticket | story_handled | approve | reject | fail")
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
    def failure(
        cls,
        execution_id: str,
        error: str,
        action: str = "fail",
        summary: str = "",
    ) -> ExecutionResult:
        return cls(
            execution_id=execution_id,
            status="failed",
            action=action,
            summary=summary or error,
            error_message=error,
        )
