"""Agent 行为继承解析器（Task 3：BehaviorResolver）。

实现 System Default Preset -> Project Override -> Agent Default Override -> Agent + WorkType Override
的三级级联继承与字段级（field-by-field）深度合并。
支持清晰的覆盖与清空语义：
- document_sources: None 表示继承；[] 显式清空全部文档源
- additional_instructions: None 表示继承；"" 显式清空继承指令
"""
from __future__ import annotations

import json
from typing import Any
from pydantic import BaseModel

from .defaults import PRESET_VERSION, get_default_payload_for_work_type
from .models import (
    AgentBehaviorConfigPayload,
    CollaborationBehavior,
    DocumentSourceConfig,
    EffectiveBehaviorConfig,
    LearningBehavior,
    PreparationBehavior,
)
from ..contract import WorkType


def _to_payload(data: Any) -> AgentBehaviorConfigPayload | None:
    if data is None:
        return None
    if isinstance(data, AgentBehaviorConfigPayload):
        return data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return None
    if isinstance(data, dict):
        try:
            return AgentBehaviorConfigPayload.model_validate(data)
        except Exception:
            return None
    return None


def merge_behavior_payload(
    base: AgentBehaviorConfigPayload,
    override: AgentBehaviorConfigPayload | dict | None,
) -> AgentBehaviorConfigPayload:
    """字段级深度合并行为配置载荷，支持显式清空（Clear）语义。"""
    if override is None:
        return base.model_copy(deep=True)

    ov = _to_payload(override)
    if ov is None:
        return base.model_copy(deep=True)

    # 1. preparation 字段合并（仅合并非 None 显式设置的值）
    base_prep_dict = (
        base.preparation.model_dump()
        if base.preparation
        else PreparationBehavior().model_dump()
    )
    if ov.preparation:
        ov_prep_dict = ov.preparation.model_dump(exclude_unset=True, exclude_none=True)
        merged_prep = PreparationBehavior(**{**base_prep_dict, **ov_prep_dict})
    else:
        merged_prep = PreparationBehavior(**base_prep_dict)

    # 2. collaboration 字段合并
    base_collab_dict = (
        base.collaboration.model_dump()
        if base.collaboration
        else CollaborationBehavior().model_dump()
    )
    if ov.collaboration:
        ov_collab_dict = ov.collaboration.model_dump(exclude_unset=True, exclude_none=True)
        merged_collab = CollaborationBehavior(**{**base_collab_dict, **ov_collab_dict})
    else:
        merged_collab = CollaborationBehavior(**base_collab_dict)

    # 3. learning 字段合并
    base_learn_dict = (
        base.learning.model_dump()
        if base.learning
        else LearningBehavior().model_dump()
    )
    if ov.learning:
        ov_learn_dict = ov.learning.model_dump(exclude_unset=True, exclude_none=True)
        merged_learn = LearningBehavior(**{**base_learn_dict, **ov_learn_dict})
    else:
        merged_learn = LearningBehavior(**base_learn_dict)

    # 4. document_sources 清空与覆盖语义：
    # - None: 未显式设置，继承 base
    # - []: 显式设置为空列表，真正清空所有数据源
    # - [item, ...]: 显式覆盖
    if ov.document_sources is not None:
        merged_sources = list(ov.document_sources)
    else:
        merged_sources = list(base.document_sources or [])

    # 5. additional_instructions 清空与覆盖语义：
    # - None: 继承 base
    # - "": 显式清空
    # - "text": 显式覆盖
    if ov.additional_instructions is not None:
        merged_inst = ov.additional_instructions
    else:
        merged_inst = base.additional_instructions

    return AgentBehaviorConfigPayload(
        preparation=merged_prep,
        collaboration=merged_collab,
        learning=merged_learn,
        document_sources=merged_sources,
        additional_instructions=merged_inst,
    )


class BehaviorResolver:
    """统一行为解析器。"""

    def __init__(self, db: Any = None):
        self.db = db

    def resolve(
        self,
        project_id: int | None = None,
        agent_id: int | None = None,
        work_type: WorkType | str | None = None,
        project_override: AgentBehaviorConfigPayload | dict | None = None,
        agent_override: AgentBehaviorConfigPayload | dict | None = None,
        agent_work_type_override: AgentBehaviorConfigPayload | dict | None = None,
    ) -> EffectiveBehaviorConfig:
        """解析并物化 EffectiveBehaviorConfig。"""
        # 1. 基础系统预设
        current = get_default_payload_for_work_type(work_type)
        sources_tracker = {"system": True, "project": False, "agent_work_type": False}

        # 2. 从 DB 查或直接使用显式传入的 project_override
        proj_ov = project_override
        if proj_ov is None and self.db is not None and project_id is not None:
            from ...features.scheduling.behavior_service import get_behavior_payload
            proj_ov = get_behavior_payload(self.db, project_id=project_id, agent_id=None, work_type=None)

        if proj_ov is not None:
            current = merge_behavior_payload(current, proj_ov)
            sources_tracker["project"] = True

        # 3. 从 DB 查或直接使用 agent_override（Agent 默认）
        ag_ov = agent_override
        if ag_ov is None and self.db is not None and agent_id is not None:
            from ...features.scheduling.behavior_service import get_behavior_payload
            ag_ov = get_behavior_payload(self.db, project_id=None, agent_id=agent_id, work_type=None)

        if ag_ov is not None:
            current = merge_behavior_payload(current, ag_ov)
            sources_tracker["agent_work_type"] = True

        # 4. 从 DB 查或直接使用 agent_work_type_override（Agent + WorkType 专属）
        ag_wt_ov = agent_work_type_override
        if ag_wt_ov is None and self.db is not None and agent_id is not None and work_type is not None:
            from ...features.scheduling.behavior_service import get_behavior_payload
            ag_wt_ov = get_behavior_payload(self.db, project_id=None, agent_id=agent_id, work_type=str(work_type))
            if ag_wt_ov is None and project_id is not None:
                ag_wt_ov = get_behavior_payload(self.db, project_id=project_id, agent_id=agent_id, work_type=str(work_type))

        if ag_wt_ov is not None:
            current = merge_behavior_payload(current, ag_wt_ov)
            sources_tracker["agent_work_type"] = True

        # Ensure non-None sections in materialized EffectiveBehaviorConfig
        prep_eff = (
            PreparationBehavior(**current.preparation.model_dump(exclude_none=True))
            if isinstance(current.preparation, BaseModel)
            else PreparationBehavior()
        )
        collab_eff = (
            CollaborationBehavior(**current.collaboration.model_dump(exclude_none=True))
            if isinstance(current.collaboration, BaseModel)
            else CollaborationBehavior()
        )
        learn_eff = (
            LearningBehavior(**current.learning.model_dump(exclude_none=True))
            if isinstance(current.learning, BaseModel)
            else LearningBehavior()
        )

        return EffectiveBehaviorConfig(
            preset="agentboard-default",
            preset_version=PRESET_VERSION,
            preparation=prep_eff,
            collaboration=collab_eff,
            learning=learn_eff,
            document_sources=current.document_sources or [],
            additional_instructions=current.additional_instructions,
            sources=sources_tracker,
        )


behavior_resolver = BehaviorResolver()