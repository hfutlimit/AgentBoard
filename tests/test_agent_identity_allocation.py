"""Agent identity, assignment, and attribution acceptance tests."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from agentboard import models
from agentboard.features.identity.service import register_user
from agentboard.features.projects.service import create_project
from agentboard.features.scheduling.service import register_agent
from agentboard.features.work_items.service import create_task


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
