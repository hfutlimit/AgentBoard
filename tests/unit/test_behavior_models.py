import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.processors.behavior.models import (
    PreparationBehavior,
    CollaborationBehavior,
    LearningBehavior,
    DocumentSourceConfig,
    AgentBehaviorConfigPayload,
    EffectiveBehaviorConfig,
)
from agentboard.processors.behavior.defaults import (
    PRESET_VERSION,
    get_default_payload_for_work_type,
)
from agentboard.processors.contract import WorkType


def test_behavior_models_defaults():
    prep = PreparationBehavior()
    assert prep.sync_code is False
    assert prep.checkout_branch is False
    assert prep.read_documents is True
    assert prep.load_memory is True
    assert prep.inspect_code is True

    collab = CollaborationBehavior()
    assert collab.read_comments is True
    assert collab.leave_summary is True
    assert collab.reply_to_review is True

    learn = LearningBehavior()
    assert learn.accepted_correction is True
    assert learn.judgment_reversal is True
    assert learn.qa_defect is True


def test_payload_serialization_roundtrip():
    payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=True, checkout_branch=True),
        collaboration=CollaborationBehavior(leave_summary=False),
        learning=LearningBehavior(qa_defect=False),
        document_sources=[
            DocumentSourceConfig(type="mcp", source_id="confluence-1", name="Confluence", scope="Backend")
        ],
        additional_instructions="Always follow PEP8.",
    )
    raw = payload.model_dump()
    reloaded = AgentBehaviorConfigPayload.model_validate(raw)

    assert reloaded.preparation.sync_code is True
    assert reloaded.preparation.checkout_branch is True
    assert reloaded.collaboration.leave_summary is False
    assert reloaded.learning.qa_defect is False
    assert len(reloaded.document_sources) == 1
    assert reloaded.document_sources[0].source_id == "confluence-1"
    assert reloaded.additional_instructions == "Always follow PEP8."


def test_work_type_presets():
    # Proposal Clarify should avoid summary/reply to review
    clarify_preset = get_default_payload_for_work_type(WorkType.PROPOSAL_CLARIFY)
    assert clarify_preset.preparation.sync_code is True
    assert clarify_preset.preparation.inspect_code is True
    assert clarify_preset.collaboration.leave_summary is False
    assert clarify_preset.collaboration.reply_to_review is False

    # Implementation preset should have sync_code, inspect_code, leave_summary, and qa_defect
    impl_preset = get_default_payload_for_work_type(WorkType.IMPLEMENTATION)
    assert impl_preset.preparation.sync_code is True
    assert impl_preset.collaboration.leave_summary is True
    assert impl_preset.learning.qa_defect is True

    # QA preset
    qa_preset = get_default_payload_for_work_type(WorkType.QA)
    assert qa_preset.preparation.sync_code is True
    assert qa_preset.learning.qa_defect is True

    # Review preset
    rev_preset = get_default_payload_for_work_type(WorkType.IMPLEMENTATION_REVIEW)
    assert rev_preset.collaboration.leave_summary is True
    assert rev_preset.learning.accepted_correction is True
