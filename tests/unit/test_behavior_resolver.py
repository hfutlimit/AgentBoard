import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.agent_runtime.behavior.models import (
    AgentBehaviorConfigPayload,
    CollaborationBehavior,
    DocumentSourceConfig,
    LearningBehavior,
    PreparationBehavior,
)
from agentboard.agent_runtime.behavior.resolver import BehaviorResolver, merge_behavior_payload
from agentboard.agent_runtime.contract import WorkType


def test_resolver_system_defaults():
    resolver = BehaviorResolver()
    eff = resolver.resolve(work_type=WorkType.IMPLEMENTATION)

    assert eff.preset == "agentboard-default"
    assert eff.preset_version == 1
    assert eff.preparation.sync_code is True
    assert eff.preparation.inspect_code is True
    assert eff.collaboration.leave_summary is True
    assert len(eff.document_sources) >= 1
    assert eff.sources == {"system": True, "project": False, "agent_work_type": False}


def test_resolver_project_override_merges_field_by_field():
    resolver = BehaviorResolver()
    project_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False, inspect_code=True),
        additional_instructions="Project guidelines here.",
    )

    eff = resolver.resolve(
        project_id=1,
        work_type=WorkType.IMPLEMENTATION,
        project_override=project_ov,
    )

    assert eff.preparation.sync_code is False
    assert eff.preparation.inspect_code is True
    assert eff.preparation.read_documents is True
    assert eff.collaboration.leave_summary is True
    assert eff.additional_instructions == "Project guidelines here."
    assert eff.sources["project"] is True


def test_resolver_agent_work_type_precedence():
    resolver = BehaviorResolver()

    project_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False),
    )
    agent_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True, sync_code=False),
    )
    agent_wt_ov = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=True, checkout_branch=True),
        additional_instructions="Agent specific prompt",
    )

    eff = resolver.resolve(
        project_id=1,
        agent_id=10,
        work_type=WorkType.IMPLEMENTATION,
        project_override=project_ov,
        agent_override=agent_ov,
        agent_work_type_override=agent_wt_ov,
    )

    assert eff.preparation.sync_code is True
    assert eff.preparation.checkout_branch is True
    assert eff.additional_instructions == "Agent specific prompt"
    assert eff.sources["system"] is True
    assert eff.sources["project"] is True
    assert eff.sources["agent_work_type"] is True


def test_resolver_empty_document_sources_explicit_clear():
    resolver = BehaviorResolver()

    # System default has document sources
    default_eff = resolver.resolve(work_type=WorkType.IMPLEMENTATION)
    assert len(default_eff.document_sources) > 0

    # User explicitly sets document_sources = [] to clear all document reading
    clear_override = AgentBehaviorConfigPayload(
        document_sources=[],
    )
    eff = resolver.resolve(
        work_type=WorkType.IMPLEMENTATION,
        agent_override=clear_override,
    )

    # Must be truly empty list, not fallen back to system default!
    assert eff.document_sources == []


def test_resolver_empty_additional_instructions_clear():
    resolver = BehaviorResolver()

    project_ov = AgentBehaviorConfigPayload(
        additional_instructions="Project global policy",
    )

    # Agent explicitly sets "" to clear inherited instructions
    agent_ov = AgentBehaviorConfigPayload(
        additional_instructions="",
    )

    eff = resolver.resolve(
        project_id=1,
        agent_id=2,
        work_type=WorkType.IMPLEMENTATION,
        project_override=project_ov,
        agent_override=agent_ov,
    )

    assert eff.additional_instructions == ""