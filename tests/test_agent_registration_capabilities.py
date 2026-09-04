"""Regression tests for capability-preserving Agent registration."""
from __future__ import annotations

import json

from agentboard.features.scheduling.service import register_agent


def test_omitted_capabilities_preserve_existing_profile(db_session_override):
    session = db_session_override
    created = register_agent(
        session,
        agent_id="capability-preserve",
        name="Configured Agent",
        capabilities=[{"name": "development", "level": 5}],
    )

    restarted = register_agent(
        session,
        agent_id=created.agent_id,
        name="Configured Agent after restart",
    )

    assert json.loads(restarted.capabilities) == [
        {"name": "development", "level": 5, "confidence": 0.5}
    ]


def test_explicit_empty_capabilities_clear_existing_profile(db_session_override):
    session = db_session_override
    created = register_agent(
        session,
        agent_id="capability-clear",
        name="Configured Agent",
        capabilities=[{"name": "review", "level": 3}],
    )

    cleared = register_agent(
        session,
        agent_id=created.agent_id,
        name="Configured Agent",
        capabilities=[],
    )

    assert json.loads(cleared.capabilities) == []


def test_new_agent_without_capabilities_is_fail_closed(db_session_override):
    created = register_agent(
        db_session_override,
        agent_id="capability-unconfigured",
        name="Unconfigured Agent",
    )

    assert json.loads(created.capabilities) == []
