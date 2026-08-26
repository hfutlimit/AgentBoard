"""Agent ????????Task 3?BehaviorResolver??

?? System Default Preset -> Project Override -> Agent Default Override -> Agent + WorkType Override
????????????field-by-field??????
"""
from __future__ import annotations

import json
from typing import Any

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
    """??????????????"""
    if override is None:
        return base.model_copy(deep=True)

    ov = _to_payload(override)
    if ov is None:
        return base.model_copy(deep=True)

    # 1. preparation ????
    base_prep = base.preparation.model_dump()
    ov_prep = ov.preparation.model_dump(exclude_unset=True)
    merged_prep = PreparationBehavior(**{**base_prep, **ov_prep})

    # 2. collaboration ????
    base_collab = base.collaboration.model_dump()
    ov_collab = ov.collaboration.model_dump(exclude_unset=True)
    merged_collab = CollaborationBehavior(**{**base_collab, **ov_collab})

    # 3. learning ????
    base_learn = base.learning.model_dump()
    ov_learn = ov.learning.model_dump(exclude_unset=True)
    merged_learn = LearningBehavior(**{**base_learn, **ov_learn})

    # 4. document_sources
    # ? override ????? sources???? override????? base
    merged_sources = ov.document_sources if ov.document_sources else base.document_sources

    # 5. additional_instructions
    merged_inst = ov.additional_instructions if ov.additional_instructions is not None else base.additional_instructions

    return AgentBehaviorConfigPayload(
        preparation=merged_prep,
        collaboration=merged_collab,
        learning=merged_learn,
        document_sources=merged_sources,
        additional_instructions=merged_inst,
    )


class BehaviorResolver:
    """????????"""

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
        """????? EffectiveBehaviorConfig?"""
        # 1. ??????
        current = get_default_payload_for_work_type(work_type)
        sources_tracker = {"system": True, "project": False, "agent_work_type": False}

        # 2. ? DB ??????????? project_override
        proj_ov = project_override
        if proj_ov is None and self.db is not None and project_id is not None:
            from ...features.scheduling.behavior_service import get_behavior_payload
            proj_ov = get_behavior_payload(self.db, project_id=project_id, agent_id=None, work_type=None)

        if proj_ov is not None:
            current = merge_behavior_payload(current, proj_ov)
            sources_tracker["project"] = True

        # 3. ? DB ?????? agent_override?Agent ???
        ag_ov = agent_override
        if ag_ov is None and self.db is not None and agent_id is not None:
            from ...features.scheduling.behavior_service import get_behavior_payload
            ag_ov = get_behavior_payload(self.db, project_id=None, agent_id=agent_id, work_type=None)

        if ag_ov is not None:
            current = merge_behavior_payload(current, ag_ov)
            sources_tracker["agent_work_type"] = True

        # 4. ? DB ?????? agent_work_type_override?Agent + WorkType ???
        ag_wt_ov = agent_work_type_override
        if ag_wt_ov is None and self.db is not None and agent_id is not None and work_type is not None:
            from ...features.scheduling.behavior_service import get_behavior_payload
            ag_wt_ov = get_behavior_payload(self.db, project_id=None, agent_id=agent_id, work_type=str(work_type))
            if ag_wt_ov is None and project_id is not None:
                ag_wt_ov = get_behavior_payload(self.db, project_id=project_id, agent_id=agent_id, work_type=str(work_type))

        if ag_wt_ov is not None:
            current = merge_behavior_payload(current, ag_wt_ov)
            sources_tracker["agent_work_type"] = True

        return EffectiveBehaviorConfig(
            preset="agentboard-default",
            preset_version=PRESET_VERSION,
            preparation=current.preparation,
            collaboration=current.collaboration,
            learning=current.learning,
            document_sources=current.document_sources,
            additional_instructions=current.additional_instructions,
            sources=sources_tracker,
        )


behavior_resolver = BehaviorResolver()
