"""Business HTTP intake + old-dispatch exclusion; no provider is simulated as real."""
import pytest
from agentboard.features.proposals import service as proposals
from agentboard.features.projects.service import create_epic
from agentboard.features.work_items.models import Task
from agentboard.features.work_items.service import try_assign_task
from agentboard.features.scheduling.service import dispatch_implementation_task, claim_story
from agentboard.core.exceptions import InvalidValue, IllegalTransition
from conftest import setup_user_project, login_token, auth_headers


def seed(client, db_session, monkeypatch):
    owner_id, project_id = setup_user_project(db_session)
    monkeypatch.setenv("AGENTBOARD_DURABLE_PROJECT_IDS", str(project_id))
    headers = auth_headers(login_token(client, db_session, owner_id))
    epic = create_epic(db_session, project_id=project_id, title="Durable intake", description="")
    proposal = proposals.create_proposal(db_session, project_id=project_id, title="Automatic chain",
        content="- [ ] Add a greeting function", author_id=owner_id, auto_create_ticket=True, target_epic_id=epic.id)
    proposals.set_proposal_status(db_session, proposal.id, "queued")
    proposals.claim_proposal(db_session, proposal.id, agent="proposal-codex")
    proposal.converged_spec = "- [ ] Add a greeting function"
    db_session.commit()
    proposals.set_proposal_status(db_session, proposal.id, "converged")
    proposals.create_ticket_request(db_session, proposal.id, type="auto_story")
    return owner_id, project_id, headers, proposal


def test_auto_materialization_ready_dag_and_no_legacy_dispatch(client, db_session, monkeypatch):
    owner_id, project_id, headers, proposal = seed(client, db_session, monkeypatch)
    url = f"/api/durable/materialize?project_id={project_id}"
    response = client.post(url, headers=headers)
    assert response.status_code == 200, response.text
    assert len(response.json()["completed_request_ids"]) == 1
    assert client.post(url, headers=headers).json()["completed_request_ids"] == []
    db_session.expire_all()
    assert proposal.story_id
    tasks = db_session.query(Task).filter_by(story_id=proposal.story_id).all()
    assert len(tasks) >= 3
    assert {t.status for t in tasks} == {"todo"}
    ready = client.get(f"/api/durable/ready-tasks?project_id={project_id}", headers=headers)
    assert ready.status_code == 200, ready.text
    items = ready.json()["items"]
    assert len(items) == 1 and items[0]["type"] == "design"
    task_id = items[0]["id"]
    assert dispatch_implementation_task(db_session, task_id) is None
    with pytest.raises(InvalidValue, match="durable"):
        try_assign_task(db_session, task_id, user_id=owner_id, source="test")
    with pytest.raises(IllegalTransition, match="durable"):
        claim_story(db_session, proposal.story_id)
    # Model a projected completed prerequisite, not an actual CLI success.
    first = db_session.get(Task, task_id)
    first.status = "done"
    db_session.commit()
    next_items = client.get(f"/api/durable/ready-tasks?project_id={project_id}", headers=headers).json()["items"]
    assert next_items and all(t["type"] == "dev" for t in next_items)
    assert all(task_id in t["dependency_ids"] for t in next_items)
    from agentboard.features.scheduling.service import complete_story
    with pytest.raises(IllegalTransition, match="unfinished"):
        complete_story(db_session, proposal.story_id)
    for task in tasks:
        task.status = "done"  # projected terminal states, not provider output
    db_session.commit()
    assert complete_story(db_session, proposal.story_id).status == "done"


def test_intake_requires_auth_membership_and_project_opt_in(client, db_session, monkeypatch):
    _, project_id, headers, _ = seed(client, db_session, monkeypatch)
    assert client.get(f"/api/durable/ready-tasks?project_id={project_id}").status_code == 401
    assert client.post(f"/api/durable/materialize?project_id={project_id}").status_code == 401
    outsider_id, _ = setup_user_project(db_session)
    outsider = auth_headers(login_token(client, db_session, outsider_id))
    assert client.get(f"/api/durable/ready-tasks?project_id={project_id}", headers=outsider).status_code == 403
    assert client.post(f"/api/durable/materialize?project_id={project_id}", headers=outsider).status_code == 403
    monkeypatch.setenv("AGENTBOARD_DURABLE_PROJECT_IDS", "")
    assert client.get(f"/api/durable/ready-tasks?project_id={project_id}", headers=headers).status_code == 409


def test_missing_or_malformed_config_does_not_silently_enable_durable(monkeypatch):
    from agentboard.features.scheduling.durable_routing import durable_project_enabled
    monkeypatch.delenv("AGENTBOARD_DURABLE_PROJECT_IDS", raising=False)
    assert not durable_project_enabled(8)
    monkeypatch.setenv("AGENTBOARD_DURABLE_PROJECT_IDS", "8,oops")
    with pytest.raises(ValueError):
        durable_project_enabled(8)


def test_crash_after_materialization_reuses_story_and_tasks(client, db_session, monkeypatch):
    from datetime import timedelta
    from agentboard.core.common.models import utc_now
    from agentboard.features.proposals.models import ProposalTicketRequest
    _, project_id, headers, proposal = seed(client, db_session, monkeypatch)
    url = f"/api/durable/materialize?project_id={project_id}"
    assert client.post(url, headers=headers).status_code == 200
    db_session.expire_all()
    story_id = proposal.story_id
    ids = [t.id for t in db_session.query(Task).filter_by(story_id=story_id).all()]
    request = db_session.query(ProposalTicketRequest).filter_by(proposal_id=proposal.id).one()
    request.status = "processing"
    request.updated_at = utc_now() - timedelta(minutes=11)
    db_session.commit()
    assert proposals.reclaim_stale_ticket_requests(db_session) == []
    assert client.post(url, headers=headers).status_code == 200
    db_session.expire_all()
    assert request.status == "done" and proposal.story_id == story_id
    assert ids == [t.id for t in db_session.query(Task).filter_by(story_id=story_id).all()]


def test_intake_respects_human_story_assignment_gates_and_paging(client, db_session, monkeypatch):
    from agentboard.features.projects.models import Story
    _, project_id, headers, proposal = seed(client, db_session, monkeypatch)
    client.post(f"/api/durable/materialize?project_id={project_id}", headers=headers)
    db_session.expire_all()
    tasks = db_session.query(Task).filter_by(story_id=proposal.story_id).order_by(Task.id).all()
    first = tasks[0]
    url = f"/api/durable/ready-tasks?project_id={project_id}"
    first.needs_human_confirmation = True
    db_session.commit()
    assert client.get(url, headers=headers).json()["items"] == []
    first.needs_human_confirmation = False
    story = db_session.get(Story, proposal.story_id)
    eligible_status = story.status
    for status in ["backlog", "blocked", "done"]:
        story.status = status
        db_session.commit()
        assert client.get(url, headers=headers).json()["items"] == []
    story.status = eligible_status
    first.current_assignment_id = 987  # fixture DB has no FK enforcement
    db_session.commit()
    assert client.get(url, headers=headers).json()["items"] == []
    first.current_assignment_id = None
    db_session.commit()
    page = client.get(url + "&limit=1", headers=headers).json()
    assert page["items"][0]["id"] == first.id and page["next_after_id"] == first.id
    # A blocked row still advances the cursor, rather than starving later rows.
    page = client.get(url + f"&limit=1&after_id={first.id}", headers=headers).json()
    assert page["items"] == [] and page["next_after_id"] == tasks[1].id


def test_projected_status_chain_completes_business_tasks_and_story(client, db_session, monkeypatch):
    from agentboard.features.work_items.service import set_status
    from agentboard.features.scheduling.service import complete_story
    owner_id, project_id, headers, proposal = seed(client, db_session, monkeypatch)
    monkeypatch.setenv("AGENTBOARD_JUDGE_AUTO", "0")
    client.post(f"/api/durable/materialize?project_id={project_id}", headers=headers)
    db_session.expire_all()
    tasks = db_session.query(Task).filter_by(story_id=proposal.story_id).order_by(Task.id).all()
    assert {task.type for task in tasks} >= {"design", "dev", "qa"}
    # Same transitions the .NET projection outbox sends. Do not force a status
    # or loosen FastAPI's mandatory review gate for a standalone QA task.
    for task in tasks:
        for status in ["in_progress", "in_review", "done"]:
            set_status(db_session, task.id, status, changed_by=owner_id,
                       status_reason="completed" if status == "done" else None)
    assert complete_story(db_session, proposal.story_id).status == "done"
