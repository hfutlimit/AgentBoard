"""Agent identity, assignment, and attribution acceptance tests."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from agentboard import models
from agentboard import api_helpers
from agentboard.core.exceptions import InvalidValue
from agentboard.features.identity.service import create_api_key, register_user
from agentboard.features.projects.service import create_project
from agentboard.features.scheduling.service import register_agent
from agentboard.features.work_items.service import create_task
from agentboard.features.work_items.service import claim_development_task


def _seed_task_and_agents(session):
    suffix = uuid.uuid4().hex[:8]
    user = register_user(
        session, username=f"allocation-{suffix}", password="password123",
    )
    project = create_project(session, name=f"Allocation {suffix}", key=f"A{suffix[:5]}")
    task = create_task(
        session, project_id=project.id, story_id=None, title="Atomic allocation",
    )
    first = register_agent(
        session, agent_id=f"agent-a-{suffix}", name="Agent A",
        roles='["developer"]', user_id=user.id,
    )
    second = register_agent(
        session, agent_id=f"agent-b-{suffix}", name="Agent B",
        roles='["developer"]', user_id=user.id,
    )
    return user, project, task, first, second


def test_allocation_models_and_attribution_columns_are_exposed():
    """Removing any allocation model/column breaks the persisted attribution contract."""
    assert hasattr(models, "TaskAssignment")
    assert hasattr(models, "TaskApplication")
    assert {
        "needed_capabilities",
        "complexity",
        "domain_tags",
        "assignment_mode",
        "current_assignment_id",
    } <= set(models.Task.__table__.columns.keys())
    assert {"agent_registry_id", "assignment_id"} <= set(
        models.AgentRun.__table__.columns.keys()
    )
    assert {"agent_registry_id", "assignment_id", "agent_ref"} <= set(
        models.TaskOutcome.__table__.columns.keys()
    )
    assert "agent_registry_id" in models.ApiKey.__table__.columns


def test_only_one_active_assignment_slot_per_task(db_session_override):
    """Dropping the unique active slot would permit scheduler/claim double allocation."""
    session = db_session_override
    user, _project, task, first, second = _seed_task_and_agents(session)
    assignment_type = models.TaskAssignment
    session.add(assignment_type(
        task_id=task.id,
        agent_registry_id=first.id,
        user_id=user.id,
        source="claim",
        status="active",
        active_slot="active",
        match_reason=json.dumps({"source": "test"}),
    ))
    session.commit()

    session.add(assignment_type(
        task_id=task.id,
        agent_registry_id=second.id,
        user_id=user.id,
        source="schedule",
        status="active",
        active_slot="active",
        match_reason=json.dumps({"source": "test"}),
    ))
    with pytest.raises(IntegrityError):
        session.commit()


def test_agent_bound_api_key_resolves_exact_agent(db_session_override):
    """Collapsing a scoped API key to user-only identity would mix sibling Agents."""
    session = db_session_override
    user, _project, _task, _first, second = _seed_task_and_agents(session)
    _item, plaintext = create_api_key(
        session,
        user_id=user.id,
        name="agent-b-key",
        permissions=["api:read", "api:write"],
        agent_ref=second.agent_id,
    )

    actor = api_helpers.resolve_actor_context(f"Bearer {plaintext}", session)

    assert actor.user_id == user.id
    assert actor.agent_registry_id == second.id
    assert actor.agent_ref == second.agent_id
    assert actor.api_key_id is not None


def test_api_key_rejects_agent_owned_by_another_user(db_session_override):
    """Allowing cross-owner binding would let one credential impersonate another Agent."""
    session = db_session_override
    owner, _project, _task, _first, _second = _seed_task_and_agents(session)
    suffix = uuid.uuid4().hex[:8]
    other = register_user(
        session, username=f"other-{suffix}", password="password123",
    )
    other_agent = register_agent(
        session,
        agent_id=f"other-agent-{suffix}",
        name="Other Agent",
        roles='["developer"]',
        user_id=other.id,
    )

    with pytest.raises(InvalidValue, match="belongs to another user"):
        create_api_key(
            session,
            user_id=owner.id,
            name="impersonation",
            permissions=["api:write"],
            agent_ref=other_agent.agent_id,
        )


def test_claim_persists_exact_agent_assignment(db_session_override):
    """Two Agents sharing one service user must not collapse to user attribution."""
    session = db_session_override
    user, _project, task, _first, second = _seed_task_and_agents(session)

    claimed = claim_development_task(
        session,
        task.id,
        user_id=user.id,
        agent_registry_id=second.id,
    )
    assignment = session.get(models.TaskAssignment, claimed.current_assignment_id)

    assert assignment is not None
    assert assignment.agent_registry_id == second.id
    assert assignment.user_id == user.id
    assert assignment.source == "claim"
    assert assignment.status == "active"
    assert assignment.active_slot == "active"


def test_claim_conflict_leaves_exactly_one_active_assignment(db_session_override):
    session = db_session_override
    user, _project, task, first, second = _seed_task_and_agents(session)
    claim_development_task(
        session, task.id, user_id=user.id, agent_registry_id=first.id,
    )

    with pytest.raises(InvalidValue, match="already claimed"):
        claim_development_task(
            session, task.id, user_id=user.id, agent_registry_id=second.id,
        )

    assert session.query(models.TaskAssignment).filter_by(
        task_id=task.id, active_slot="active",
    ).count() == 1


def test_agent_cannot_directly_claim_arbitrated_task(db_session_override):
    session = db_session_override
    user, _project, task, first, _second = _seed_task_and_agents(session)
    task.assignment_mode = "arbitrated"
    session.commit()

    with pytest.raises(InvalidValue, match="apply"):
        claim_development_task(
            session, task.id, user_id=user.id, agent_registry_id=first.id,
        )

    assert task.status == "todo"
    assert session.query(models.TaskAssignment).count() == 0
