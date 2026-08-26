"""Agent 行为配置模型（AgentBehaviorConfig Models）。

定义语义化行为配置项：Preparation, Collaboration, Learning, Sources 与最终生效配置。
支持完整默认载荷与局部覆盖（Partial Overrides）语义。
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class PreparationBehavior(BaseModel):
    """任务准备阶段行为。"""
    sync_code: bool = Field(default=False, description="是否在工作前同步最新代码")
    checkout_branch: bool = Field(default=False, description="是否自动检出关联分支")
    read_documents: bool = Field(default=True, description="是否查阅需求/设计文档")
    load_memory: bool = Field(default=True, description="是否查阅项目历史经验记忆")
    inspect_code: bool = Field(default=True, description="是否在发问或修改前检索现有源码")


class CollaborationBehavior(BaseModel):
    """协同与留痕行为。"""
    read_comments: bool = Field(default=True, description="是否查阅历史评论与讨论记录")
    leave_summary: bool = Field(default=True, description="是否在工作完成时留下结构化总结")
    reply_to_review: bool = Field(default=True, description="是否在收到审查意见时规范回应")


class LearningBehavior(BaseModel):
    """纠错与持续学习行为。"""
    accepted_correction: bool = Field(default=True, description="是否在采纳审查后提炼教训")
    judgment_reversal: bool = Field(default=True, description="是否在误判更正后反思沉淀")
    qa_defect: bool = Field(default=True, description="是否在 QA 发现缺陷后沉淀用例")


class DocumentSourceConfig(BaseModel):
    """文档与数据源配置项。"""
    type: Literal["project_documents", "linked_documents", "mcp"] = Field(
        ..., description="数据源类型：项目文档、关联工单文档或外部 MCP 服务"
    )
    source_id: str | None = Field(default=None, description="MCP 数据源标识或工具名称")
    name: str | None = Field(default=None, description="数据源显示名称")
    scope: str | None = Field(default=None, description="数据源检索范围说明")


class PartialPreparationBehavior(BaseModel):
    sync_code: bool | None = None
    checkout_branch: bool | None = None
    read_documents: bool | None = None
    load_memory: bool | None = None
    inspect_code: bool | None = None


class PartialCollaborationBehavior(BaseModel):
    read_comments: bool | None = None
    leave_summary: bool | None = None
    reply_to_review: bool | None = None


class PartialLearningBehavior(BaseModel):
    accepted_correction: bool | None = None
    judgment_reversal: bool | None = None
    qa_defect: bool | None = None


class AgentBehaviorConfigPayload(BaseModel):
    """用户或项目配置的行为载荷（支持全量与局部覆盖）。"""
    preparation: PreparationBehavior | PartialPreparationBehavior | None = None
    collaboration: CollaborationBehavior | PartialCollaborationBehavior | None = None
    learning: LearningBehavior | PartialLearningBehavior | None = None
    # None 表示继承上级；[] 表示明确清空所有文档数据源
    document_sources: list[DocumentSourceConfig] | None = None
    # None 表示继承上级；"" 或 字符串 表示显式覆盖/清空
    additional_instructions: str | None = None


class EffectiveBehaviorConfig(BaseModel):
    """最终解析物化的运行时行为配置实体。"""
    preset: str = Field(default="agentboard-default", description="基准预设名称")
    preset_version: int = Field(default=1, description="预设版本号")
    preparation: PreparationBehavior = Field(default_factory=PreparationBehavior)
    collaboration: CollaborationBehavior = Field(default_factory=CollaborationBehavior)
    learning: LearningBehavior = Field(default_factory=LearningBehavior)
    document_sources: list[DocumentSourceConfig] = Field(default_factory=list)
    additional_instructions: str | None = Field(default=None)
    sources: dict[str, bool] = Field(
        default_factory=lambda: {"system": True, "project": False, "agent_work_type": False},
        description="各层配置贡献标记",
    )