"""Golden Happy Path: AUTO Proposal → deterministic Story DAG → Story done.

This test drives the production REST/service orchestration with an in-memory DB
and broker.  CLI execution is represented by each assigned Agent calling the
same submit/review endpoints used by real Workers.
"""
from __future__ import annotations

import uuid

from agentboard.core.common.enums import Status
from agentboard.core.common.models import utc_now
from agentboard.features.identity.service import register_user
from agentboard.features.projects.models import (
    Agent,
    AgentInstance,
    Epic,
    ProjectMember,
    Story,
)
from agentboard.features.projects.service import create_epic
from agentboard.features.proposals import service as proposal_service
from agentboard.features.proposals.models import ProposalTicketRequest
from agentboard.features.scheduling.models import TaskAssignment
from agentboard.features.scheduling.service import register_worker
from agentboard.features.work_items.models import Task, TaskDependency

from conftest import auth_headers, login_token, setup_user_project


def _register_runnable_agent(
    db_session,
    broker,
    *,
    project_id: int,
    user_id: int,
    name: str,
    executor_type: str,
    roles: str = "[]",
    online: bool = True,
) -> Agent:
    worker_id = f"worker-{name}"
    agent_id = f"agent-{name}"
    register_worker(db_session, worker_id=worker_id, hostname="golden-e2e")
    agent = Agent(
        agent_id=agent_id,
        name=name,
        user_id=user_id,
        roles=roles,
        capabilities="[]",
        cli_command="",
        model="",
        enabled=True,
        online=online,
        last_heartbeat=utc_now() if online else None,
    )
    db_session.add(agent)
    db_session.flush()
    db_session.add(AgentInstance(
        worker_id=worker_id,
        agent_id=agent_id,
        executor_type=executor_type,
        cli_command="",
        model="",
        auth_key="",
        enabled=True,
        online=online,
        last_heartbeat=utc_now() if online else None,
    ))
    db_session.commit()
    broker.declare_agent_queue(worker_id)
    return agent


def _submit_assign_and_approve(
    client,
    db_session,
    *,
    task_id: int,
    implementer_headers: dict,
    owner_headers: dict,
    headers_by_user_id: dict[int, dict],
) -> int:
    response = client.post(
        f"/api/tasks/{task_id}/submit-review", headers=implementer_headers,
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/tasks/{task_id}/assign-reviewer", headers=owner_headers,
    )
    assert response.status_code in (200, 201), response.text
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task is not None and task.reviewer_id is not None
    reviewer_id = int(task.reviewer_id)
    response = client.post(
        f"/api/tasks/{task_id}/review",
        json={"verdict": "approve", "comment": "golden e2e approve"},
        headers=headers_by_user_id[reviewer_id],
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == Status.DONE.value
    return reviewer_id


def test_auto_proposal_reaches_story_done_with_dynamic_qa_separation(
    db_session, client, broker,
):
    owner_id, project_id = setup_user_project(db_session, role="owner")
    second_user = register_user(
        db_session,
        username=f"golden-{uuid.uuid4().hex[:8]}",
        password="test1234",
    )
    third_user = register_user(
        db_session,
        username=f"golden-{uuid.uuid4().hex[:8]}",
        password="test1234",
    )
    db_session.add_all([
        ProjectMember(
            project_id=project_id, user_id=second_user.id, role="member",
        ),
        ProjectMember(
            project_id=project_id, user_id=third_user.id, role="member",
        ),
    ])
    db_session.commit()

    owner_headers = auth_headers(login_token(client, db_session, owner_id))
    second_headers = auth_headers(login_token(client, db_session, second_user.id))
    third_headers = auth_headers(login_token(client, db_session, third_user.id))
    headers_by_user_id = {
        owner_id: owner_headers,
        second_user.id: second_headers,
        third_user.id: third_headers,
    }

    # All three Agents have no business role.  executor_type only selects the
    # physical runner and must not limit Design/Dev/QA/Review eligibility.
    first_agent = _register_runnable_agent(
        db_session,
        broker,
        project_id=project_id,
        user_id=owner_id,
        name="first",
        executor_type="codex",
    )
    second_agent = _register_runnable_agent(
        db_session,
        broker,
        project_id=project_id,
        user_id=second_user.id,
        name="second",
        executor_type="workbuddy",
        online=False,
    )
    third_agent = _register_runnable_agent(
        db_session,
        broker,
        project_id=project_id,
        user_id=third_user.id,
        name="third",
        executor_type="minimax",
    )

    target_epic = create_epic(
        db_session,
        project_id=project_id,
        title="Golden target",
        description="",
    )
    proposal = proposal_service.create_proposal(
        db_session,
        project_id=project_id,
        title="Golden happy path",
        content="- [ ] Implement the golden slice",
        author_id=owner_id,
        auto_create_ticket=True,
        target_epic_id=target_epic.id,
    )
    proposal_service.set_proposal_status(db_session, proposal.id, "queued")
    proposal_service.claim_proposal(db_session, proposal.id, agent="golden-grill")
    proposal.converged_spec = "- [ ] Implement the golden slice"
    db_session.commit()
    proposal_service.set_proposal_status(db_session, proposal.id, "converged")
    request = proposal_service.create_ticket_request(
        db_session, proposal.id, type="auto_story",
    )

    response = client.post(
        f"/api/ticket-requests/{request.id}/execute", headers=owner_headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    story_id = int(result["ticket"]["id"])
    assert result["request"]["type"] == "auto_story"
    assert result["request"]["resolved_type"] == "story"
    assert len(result["dispatched_task_ids"]) == 1
    assert result["deferred_task_ids"] == []

    # Idempotent replay reuses exactly the same Story/DAG.
    story_count_before_replay = db_session.query(Story).filter(
        Story.epic_id == target_epic.id,
    ).count()
    task_count_before_replay = db_session.query(Task).filter(
        Task.story_id == story_id,
    ).count()
    replay = client.post(
        f"/api/ticket-requests/{request.id}/execute", headers=owner_headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["ticket"]["id"] == story_id
    assert db_session.query(Story).filter(
        Story.epic_id == target_epic.id,
    ).count() == story_count_before_replay
    assert db_session.query(Task).filter(
        Task.story_id == story_id,
    ).count() == task_count_before_replay

    tasks = db_session.query(Task).filter(Task.story_id == story_id).all()
    assert [task.type for task in tasks].count("design") == 1
    assert [task.type for task in tasks].count("dev") == 1
    assert [task.type for task in tasks].count("qa") == 1
    assert len(tasks) == 3  # no create_story default-task leakage
    assert all(task.needs_human_confirmation is False for task in tasks)
    assert db_session.query(TaskDependency).filter(
        TaskDependency.task_id.in_([task.id for task in tasks]),
    ).count() == 2

    design = next(task for task in tasks if task.type == "design")
    dev = next(task for task in tasks if task.type == "dev")
    qa = next(task for task in tasks if task.type == "qa")
    db_session.refresh(design)
    assert design.status == Status.IN_PROGRESS.value
    design_assignment = db_session.get(TaskAssignment, design.current_assignment_id)
    assert design_assignment.agent_registry_id == first_agent.id

    design_reviewer_id = _submit_assign_and_approve(
        client,
        db_session,
        task_id=design.id,
        implementer_headers=owner_headers,
        owner_headers=owner_headers,
        headers_by_user_id=headers_by_user_id,
    )
    # The temporarily offline second Agent cannot review; this proves the
    # third generic Agent participates in the real orchestration path.
    assert design_reviewer_id == third_user.id

    second_agent.online = True
    second_agent.last_heartbeat = utc_now()
    second_instance = db_session.query(AgentInstance).filter(
        AgentInstance.agent_id == second_agent.agent_id,
    ).one()
    second_instance.online = True
    second_instance.last_heartbeat = utc_now()
    db_session.commit()

    db_session.expire_all()
    dev = db_session.get(Task, dev.id)
    assert dev.status == Status.IN_PROGRESS.value
    dev_assignment = db_session.get(TaskAssignment, dev.current_assignment_id)
    assert dev_assignment.agent_registry_id == first_agent.id

    dev_reviewer_id = _submit_assign_and_approve(
        client,
        db_session,
        task_id=dev.id,
        implementer_headers=owner_headers,
        owner_headers=owner_headers,
        headers_by_user_id=headers_by_user_id,
    )
    assert dev_reviewer_id == second_user.id

    db_session.expire_all()
    qa = db_session.get(Task, qa.id)
    assert qa.status == Status.IN_PROGRESS.value
    qa_assignment = db_session.get(TaskAssignment, qa.current_assignment_id)
    # The upstream Dev implementer must not execute QA.  No fallback may
    # silently choose it when another runnable Agent exists.
    assert qa_assignment.agent_registry_id == second_agent.id
    assert qa_assignment.agent_registry_id != dev_assignment.agent_registry_id
    assert first_agent.roles == second_agent.roles == third_agent.roles == "[]"

    qa_reviewer_id = _submit_assign_and_approve(
        client,
        db_session,
        task_id=qa.id,
        implementer_headers=second_headers,
        owner_headers=owner_headers,
        headers_by_user_id=headers_by_user_id,
    )
    # Upstream Dev Agent is allowed to review QA because it did not implement
    # the QA task itself.
    assert qa_reviewer_id == owner_id

    db_session.expire_all()
    story = db_session.get(Story, story_id)
    request = db_session.get(ProposalTicketRequest, request.id)
    proposal = proposal_service.get_proposal(db_session, proposal.id)
    assert story.status == "done"
    assert request.status == "done"
    assert proposal.ticket_type == "story"
    assert proposal.ticket_id == story_id
    assert proposal.target_epic_id == target_epic.id
    assert db_session.query(Epic).filter(Epic.id == target_epic.id).one().id == target_epic.id
