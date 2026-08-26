"""统一工作项与执行契约（Unified Execution Contract）。

语言中立契约：定义 Worker 与 Server 之间传递的执行命令、工作类型与结构化结果。
解耦业务领域模型（Proposal / Epic / Story / Task 保持独立表与领域逻辑）与底层执行管道。
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


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

    # 向后兼容别名（以兼容旧配置与历史代码）
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
