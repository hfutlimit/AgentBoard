from __future__ import annotations

from typing import Any
from .models import (
    AgentBehaviorConfigPayload,
    CollaborationBehavior,
    DocumentSourceConfig,
    EffectiveBehaviorConfig,
    LearningBehavior,
    PreparationBehavior,
)
from ..contract import WorkType

PRESET_VERSION = 1


def _normalize_work_type(work_type: WorkType | str | None) -> str:
    if work_type is None:
        return ""
    if isinstance(work_type, WorkType):
        return work_type.value.lower()
    val = str(work_type).lower().strip()
    if "." in val:
        val = val.split(".")[-1]
    return val


def get_default_payload_for_work_type(work_type: WorkType | str | None) -> AgentBehaviorConfigPayload:
    """?? WorkType ?? AgentBoard ?????????"""
    wt = _normalize_work_type(work_type)

    # 1. ?????Proposal Clarification?
    if wt in (WorkType.PROPOSAL_CLARIFY.value, "proposal_clarify", "clarify"):
        return AgentBehaviorConfigPayload(
            preparation=PreparationBehavior(
                sync_code=True,
                checkout_branch=False,
                read_documents=True,
                load_memory=True,
                inspect_code=True,
            ),
            collaboration=CollaborationBehavior(
                read_comments=True,
                leave_summary=False,
                reply_to_review=False,
            ),
            learning=LearningBehavior(
                accepted_correction=True,
                judgment_reversal=True,
                qa_defect=False,
            ),
            document_sources=[
                DocumentSourceConfig(type="project_documents"),
                DocumentSourceConfig(type="linked_documents"),
            ],
            additional_instructions=None,
        )

    # 2. ?????Proposal Conversion?
    if wt in (WorkType.PROPOSAL_CONVERT.value, "proposal_convert", "convert"):
        return AgentBehaviorConfigPayload(
            preparation=PreparationBehavior(
                sync_code=True,
                checkout_branch=False,
                read_documents=True,
                load_memory=True,
                inspect_code=True,
            ),
            collaboration=CollaborationBehavior(
                read_comments=True,
                leave_summary=True,
                reply_to_review=False,
            ),
            learning=LearningBehavior(
                accepted_correction=True,
                judgment_reversal=True,
                qa_defect=False,
            ),
            document_sources=[
                DocumentSourceConfig(type="project_documents"),
                DocumentSourceConfig(type="linked_documents"),
            ],
            additional_instructions=None,
        )

    # 3. ??/?????Design?
    if wt in (WorkType.DESIGN.value, "design"):
        return AgentBehaviorConfigPayload(
            preparation=PreparationBehavior(
                sync_code=True,
                checkout_branch=False,
                read_documents=True,
                load_memory=True,
                inspect_code=True,
            ),
            collaboration=CollaborationBehavior(
                read_comments=True,
                leave_summary=True,
                reply_to_review=True,
            ),
            learning=LearningBehavior(
                accepted_correction=True,
                judgment_reversal=True,
                qa_defect=False,
            ),
            document_sources=[
                DocumentSourceConfig(type="project_documents"),
                DocumentSourceConfig(type="linked_documents"),
            ],
            additional_instructions=None,
        )

    # 4. ?????Implementation / Dev?
    if wt in (WorkType.IMPLEMENTATION.value, WorkType.TASK_IMPLEMENT.value, "implementation", "dev", "task"):
        return AgentBehaviorConfigPayload(
            preparation=PreparationBehavior(
                sync_code=True,
                checkout_branch=False,
                read_documents=True,
                load_memory=True,
                inspect_code=True,
            ),
            collaboration=CollaborationBehavior(
                read_comments=True,
                leave_summary=True,
                reply_to_review=True,
            ),
            learning=LearningBehavior(
                accepted_correction=True,
                judgment_reversal=True,
                qa_defect=True,
            ),
            document_sources=[
                DocumentSourceConfig(type="project_documents"),
                DocumentSourceConfig(type="linked_documents"),
            ],
            additional_instructions=None,
        )

    # 5. ?????QA?
    if wt in (WorkType.QA.value, "qa", "test"):
        return AgentBehaviorConfigPayload(
            preparation=PreparationBehavior(
                sync_code=True,
                checkout_branch=False,
                read_documents=True,
                load_memory=True,
                inspect_code=True,
            ),
            collaboration=CollaborationBehavior(
                read_comments=True,
                leave_summary=True,
                reply_to_review=True,
            ),
            learning=LearningBehavior(
                accepted_correction=True,
                judgment_reversal=True,
                qa_defect=True,
            ),
            document_sources=[
                DocumentSourceConfig(type="project_documents"),
                DocumentSourceConfig(type="linked_documents"),
            ],
            additional_instructions=None,
        )

    # 6. ???Design Review / Implementation Review / QA Review?
    if wt in (
        WorkType.DESIGN_REVIEW.value,
        WorkType.IMPLEMENTATION_REVIEW.value,
        WorkType.QA_REVIEW.value,
        WorkType.TASK_REVIEW.value,
        "review",
        "design_review",
        "implementation_review",
        "qa_review",
    ):
        return AgentBehaviorConfigPayload(
            preparation=PreparationBehavior(
                sync_code=True,
                checkout_branch=False,
                read_documents=True,
                load_memory=True,
                inspect_code=True,
            ),
            collaboration=CollaborationBehavior(
                read_comments=True,
                leave_summary=True,
                reply_to_review=True,
            ),
            learning=LearningBehavior(
                accepted_correction=True,
                judgment_reversal=True,
                qa_defect=False,
            ),
            document_sources=[
                DocumentSourceConfig(type="project_documents"),
                DocumentSourceConfig(type="linked_documents"),
            ],
            additional_instructions=None,
        )

    # ??????
    return AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(
            sync_code=True,
            checkout_branch=False,
            read_documents=True,
            load_memory=True,
            inspect_code=True,
        ),
        collaboration=CollaborationBehavior(
            read_comments=True,
            leave_summary=True,
            reply_to_review=True,
        ),
        learning=LearningBehavior(
            accepted_correction=True,
            judgment_reversal=True,
            qa_defect=True,
        ),
        document_sources=[
            DocumentSourceConfig(type="project_documents"),
            DocumentSourceConfig(type="linked_documents"),
        ],
        additional_instructions=None,
    )
