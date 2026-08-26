import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.agent_runtime.behavior.defaults import get_default_payload_for_work_type
from agentboard.agent_runtime.behavior.models import (
    AgentBehaviorConfigPayload,
    DocumentSourceConfig,
    EffectiveBehaviorConfig,
    PreparationBehavior,
)
from agentboard.agent_runtime.behavior.prompt_builder import PromptBuilder, prompt_builder
from agentboard.agent_runtime.behavior.resolver import behavior_resolver
from agentboard.agent_runtime.contract import WorkType


def test_proposal_clarify_prompt_composition():
    eff = behavior_resolver.resolve(work_type=WorkType.PROPOSAL_CLARIFY)
    prompt = prompt_builder.build(
        work_type=WorkType.PROPOSAL_CLARIFY,
        behavior=eff,
        context={"raw_context_summary": "Proposal #1: Add dark mode support."},
    )

    assert "核心职责：需求澄清" in prompt
    assert "代码审查" in prompt
    assert "凡是能从现有代码中直接查明的事实，绝不得向用户发问" in prompt
    assert "【平台契约】" in prompt


def test_disabled_sync_code_removes_instruction():
    custom_payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False, inspect_code=True)
    )
    eff = behavior_resolver.resolve(
        work_type=WorkType.IMPLEMENTATION,
        agent_override=custom_payload,
    )
    prompt = prompt_builder.build(work_type=WorkType.IMPLEMENTATION, behavior=eff)

    assert "代码审查" in prompt
    assert "代码同步" not in prompt


def test_checkout_branch_precedes_sync_code():
    custom_payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True, sync_code=True)
    )
    eff = behavior_resolver.resolve(
        work_type=WorkType.IMPLEMENTATION,
        agent_override=custom_payload,
    )
    prompt = prompt_builder.build(
        work_type=WorkType.IMPLEMENTATION,
        behavior=eff,
        context={"branch": "feat/new-ui"},
    )

    idx_checkout = prompt.find("分支准备")
    idx_sync = prompt.find("代码同步")
    assert idx_checkout != -1
    assert idx_sync != -1
    assert idx_checkout < idx_sync


def test_preview_mode_excludes_platform_contract():
    eff = behavior_resolver.resolve(work_type=WorkType.DESIGN)
    preview = prompt_builder.build(
        work_type=WorkType.DESIGN,
        behavior=eff,
        preview_mode=True,
    )

    assert "【平台契约】" not in preview
    assert "核心职责：架构与技术设计" in preview
    assert "执行行为指引" in preview


def test_mcp_sources_and_learnings_rendering():
    custom_payload = AgentBehaviorConfigPayload(
        document_sources=[
            DocumentSourceConfig(type="mcp", source_id="confluence", name="Confluence Docs", scope="Architecture")
        ],
        additional_instructions="Strict typing required.",
    )
    eff = behavior_resolver.resolve(
        work_type=WorkType.IMPLEMENTATION,
        agent_override=custom_payload,
    )
    learnings = [
        {"category": "accepted_review_feedback", "summary": "Verify DB constraints", "lesson": "Always check unique constraints."}
    ]
    prompt = prompt_builder.build(
        work_type=WorkType.IMPLEMENTATION,
        behavior=eff,
        learnings=learnings,
    )

    assert "Confluence Docs" in prompt
    assert "Strict typing required." in prompt
    assert "【历史项目经验（Project Learnings）】" in prompt
    assert "Verify DB constraints" in prompt