"""Capability profile and deterministic matching acceptance tests."""
from __future__ import annotations

import json
import uuid

import pytest

from agentboard import models
from agentboard.core.exceptions import InvalidValue
from agentboard.features.identity.service import register_user
from agentboard.features.projects.service import create_project
from agentboard.features.projects.service import add_project_member
from agentboard.features.scheduling.matching import (
    normalize_capabilities,
    rank_agents_for_task,
    score_agent_for_task,
)
from agentboard.features.scheduling.service import register_agent
from agentboard.features.work_items.service import create_task
from agentboard.features.work_items.service import apply_for_task, arbitrate_task


def _seed_profiles(session):
    suffix = uuid.uuid4().hex[:8]
    user = register_user(
        session, username=f"matcher-{suffix}", password="password123",
    )
    project = create_project(session, name=f"Matcher {suffix}", key=f"M{suffix[:5]}")
    task = create_task(
        session,
        project_id=project.id,
        story_id=None,
        title="Build the responsive editor",
        type="dev",
        needed_capabilities=[{"name": "frontend", "minimum_level": 4}],
        complexity=4,
        domain_tags=["UI", "Editor", "ui"],
        assignment_mode="arbitrated",
    )
    senior = register_agent(
        session,
        agent_id=f"frontend-senior-{suffix}",
        name="Frontend Senior",
        roles='["developer", "reviewer"]',
        capabilities=[
            {"name": "Frontend", "level": 5, "confidence": 0.9},
        ],
        user_id=user.id,
    )
    junior = register_agent(
        session,
        agent_id=f"frontend-junior-{suffix}",
        name="Frontend Junior",
        roles='["developer"]',
        capabilities=[
            {"name": "frontend", "level": 4, "confidence": 0.7},
        ],
        user_id=user.id,
    )
    backend = register_agent(
        session,
        agent_id=f"backend-{suffix}",
        name="Backend",
        roles='["developer"]',
        capabilities='["backend"]',
        user_id=user.id,
    )
    session.add_all([
        models.TaskOutcome(
            task_id=create_task(
                session, project_id=project.id, story_id=None,
                title="Senior history", type="dev",
            ).id,
            project_id=project.id,
            agent_id=user.id,
            agent_registry_id=senior.id,
            agent_ref=senior.agent_id,
            task_type="dev",
            score=0.95,
        ),
        models.TaskOutcome(
            task_id=create_task(
                session, project_id=project.id, story_id=None,
                title="Junior history", type="dev",
            ).id,
            project_id=project.id,
            agent_id=user.id,
            agent_registry_id=junior.id,
            agent_ref=junior.agent_id,
            task_type="dev",
            score=0.55,
        ),
    ])
    session.commit()
    return task, senior, junior, backend


def test_legacy_capability_normalizes_to_structured_entry():
    assert normalize_capabilities('["Frontend"]') == [
        {"name": "frontend", "level": 3, "confidence": 0.5},
    ]


def test_task_and_agent_profiles_are_normalized_at_write_boundary(db_session_override):
    session = db_session_override
    task, senior, _junior, _backend = _seed_profiles(session)

    assert json.loads(task.needed_capabilities) == [
        {"name": "frontend", "minimum_level": 4},
    ]
    assert json.loads(task.domain_tags) == ["ui", "editor"]
    assert task.complexity == 4
    assert task.assignment_mode == "arbitrated"
    assert json.loads(senior.capabilities) == [
        {"name": "frontend", "level": 5, "confidence": 0.9},
    ]


def test_matching_prefers_capability_history_and_proficiency(db_session_override):
    session = db_session_override
    task, senior, _junior, _backend = _seed_profiles(session)

    ranked = rank_agents_for_task(session, task, role="developer")

    assert ranked[0].agent.id == senior.id
    assert ranked[0].result.eligible is True
    assert ranked[0].result.score > ranked[1].result.score
    assert ranked[0].result.components["history"] == 0.95


def test_active_work_reduces_match_score(db_session_override):
    session = db_session_override
    task, senior, _junior, _backend = _seed_profiles(session)
    busy_task = create_task(
        session, project_id=task.project_id, story_id=None, title="Busy work",
    )
    session.add(models.TaskAssignment(
        task_id=busy_task.id,
        agent_registry_id=senior.id,
        user_id=senior.user_id,
        source="claim",
        status="active",
        active_slot="active",
    ))
    session.commit()

    result = score_agent_for_task(session, senior, task)

    assert result.components["active_load"] == 1
    assert result.components["load_factor"] == 0.5


def test_missing_required_capability_is_ineligible(db_session_override):
    session = db_session_override
    task, _senior, _junior, backend = _seed_profiles(session)

    result = score_agent_for_task(session, backend, task)

    assert result.eligible is False
    assert "frontend" in result.reason


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("complexity", 6, "complexity"),
        ("assignment_mode", "random", "assignment_mode"),
        ("needed_capabilities", [{"name": "frontend", "minimum_level": 9}], "minimum_level"),
    ],
)
def test_invalid_task_profiles_are_rejected(db_session_override, field, value, message):
    session = db_session_override
    project = create_project(session, name=f"Invalid {uuid.uuid4().hex[:8]}")
    kwargs = {field: value}

    with pytest.raises(InvalidValue, match=message):
        create_task(
            session, project_id=project.id, story_id=None, title="Invalid profile",
            **kwargs,
        )


def test_arbitration_accepts_highest_match_and_rejects_others(db_session_override):
    session = db_session_override
    task, senior, junior, _backend = _seed_profiles(session)

    senior_application = apply_for_task(
        session,
        task.id,
        user_id=senior.user_id,
        agent_registry_id=senior.id,
    )
    junior_application = apply_for_task(
        session,
        task.id,
        user_id=junior.user_id,
        agent_registry_id=junior.id,
    )
    assigned_task, assignment, winner = arbitrate_task(session, task.id)

    assert winner.id == senior_application.id
    assert assignment.agent_registry_id == senior.id
    assert assigned_task.current_assignment_id == assignment.id
    session.refresh(senior_application)
    session.refresh(junior_application)
    assert senior_application.status == "accepted"
    assert junior_application.status == "rejected"
    assert senior_application.resolved_at is not None


def test_application_is_idempotently_rescored(db_session_override):
    session = db_session_override
    task, senior, _junior, _backend = _seed_profiles(session)

    first = apply_for_task(
        session, task.id, user_id=senior.user_id, agent_registry_id=senior.id,
    )
    second = apply_for_task(
        session, task.id, user_id=senior.user_id, agent_registry_id=senior.id,
    )

    assert second.id == first.id
    assert session.query(models.TaskApplication).filter_by(
        task_id=task.id, agent_registry_id=senior.id,
    ).count() == 1


def test_apply_and_arbitrate_rest_flow_uses_credential_agent(
    db_session_override, monkeypatch,
):
    from fastapi.testclient import TestClient

    from agentboard import auth
    from agentboard.api import app
    from agentboard.core.infrastructure.database import get_session
    from agentboard.features.identity.service import create_api_key

    session = db_session_override
    task, senior, _junior, _backend = _seed_profiles(session)
    add_project_member(
        session, project_id=task.project_id, user_id=senior.user_id, role="owner",
    )
    _key, plaintext = create_api_key(
        session,
        user_id=senior.user_id,
        name="matching-agent",
        permissions=["api:write"],
        agent_ref=senior.agent_id,
    )

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "0")
    try:
        client = TestClient(app)
        applied = client.post(
            f"/api/tasks/{task.id}/apply",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["agent_registry_id"] == senior.id

        arbitrated = client.post(
            f"/api/tasks/{task.id}/arbitrate",
            headers={
                "Authorization": f"Bearer {auth.make_token(senior.user_id)}",
            },
        )
        assert arbitrated.status_code == 200, arbitrated.text
        assert arbitrated.json()["assignment"]["agent_registry_id"] == senior.id
    finally:
        app.dependency_overrides.pop(get_session, None)
