"""Server-owned planning (``serverplan`` protocol).

Since the Server took over the state machine the Worker no longer scans
business state or posts offers. These cases pin the Server-side planner that
replaced it, including the decision semantics that used to live in the C#
``WorkerOwnedTests`` (``WorkPlanner.Next`` / ``TicketPlan`` / ``AddQaFollowup``).
"""
import json
import uuid

import pytest
from conftest import auth_headers, login_token, setup_user_project

from agentboard.features.projects.models import (
    Agent, AgentInstance, ProjectMember, Story, Worker, WorkerProjectMapping,
)
from agentboard.features.scheduling import worker_planner as planner
from agentboard.features.scheduling.worker_work import default_ticket_plan
from agentboard.features.scheduling.worker_work_models import (
    WorkerDiscussion, WorkerWork,
)
from agentboard.features.work_items.service import create_task


class Stub:
    """Detached stand-in for a business row the planner must reject early."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


@pytest.fixture
def plan_env(client, db_session, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_WORKER_OWNED_ENABLED", "1")
    uid, pid = setup_user_project(db_session)
    from agentboard.features.projects.service import create_epic, create_story

    epic = create_epic(db_session, project_id=pid, title="Planner fixtures")
    story = create_story(
        db_session, epic_id=epic.id, title="Planner story",
        create_default_tasks=False, created_by_user_id=uid,
    )
    story.status = "in_progress"
    story.owner_user_id = uid
    db_session.commit()
    return uid, pid, story


def make_task(db, env, task_type="dev", **overrides):
    uid, pid, story = env
    task = create_task(
        db, project_id=pid, story_id=story.id, title=task_type, type=task_type,
        owner_user_id=uid, needs_human_confirmation=False,
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    db.commit()
    return task


def map_worker(db, env, *, kinds, online=True, executor_type="codex", worker_id="w-plan"):
    uid, pid, _ = env
    db.add(Worker(worker_id=worker_id, hostname="planner-host", status="active"))
    agent = Agent(
        agent_id=f"{worker_id}-agent", name=f"{worker_id}-agent", user_id=uid, roles="[]",
        capabilities=json.dumps([{"name": kind, "level": 1} for kind in kinds]),
    )
    db.add(agent)
    db.flush()
    db.add(AgentInstance(
        worker_id=worker_id, agent_id=agent.agent_id, executor_type=executor_type,
        enabled=True, online=online,
    ))
    db.add(WorkerProjectMapping(worker_id=worker_id, project_id=pid, enabled=True))
    db.commit()


# ---------------------------------------------------------------------------
# offer derivation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_type,status,expected", [
    ("design", "todo", "design"),
    ("design", "in_review", "design_review"),
    ("dev", "in_review", "dev_review"),
    ("bug", "todo", "dev"),
    ("bug", "in_review", "dev_review"),
    ("qa", "todo", "qa"),
    ("qa", "in_review", "qa_review"),
])
def test_task_type_and_status_select_the_work_kind(db_session, plan_env, task_type, status, expected):
    task = make_task(db_session, plan_env, task_type, status=status, review_round=2)

    offer = planner.offer_for(db_session, plan_env[1], "task", task)

    assert offer is not None
    assert offer.kind == expected
    assert offer.iteration == 2


def test_not_ready_or_human_gated_work_is_not_offered(db_session, plan_env):
    blocked = make_task(db_session, plan_env, "qa", status="todo")
    blocked.needs_human_confirmation = True
    db_session.commit()
    assert planner.offer_for(db_session, plan_env[1], "task", blocked) is None

    # A finished Task is never re-offered.
    done = make_task(db_session, plan_env, "design", status="done")
    assert planner.offer_for(db_session, plan_env[1], "task", done) is None


def test_unsupported_historical_task_type_is_ignored(db_session, plan_env):
    historical = Stub(type="ticket", status="todo", story_id=plan_env[2].id,
                      needs_human_confirmation=False, review_round=0)
    assert planner.offer_for(db_session, plan_env[1], "task", historical) is None


def test_proposal_is_offered_from_queued_and_answered_only(db_session, plan_env):
    assert planner.offer_for(db_session, plan_env[1], "proposal",
                             Stub(id=1, status="queued", current_round=3)).iteration == 3
    assert planner.offer_for(db_session, plan_env[1], "proposal",
                             Stub(id=1, status="converged", current_round=1)) is None


def test_discussion_turn_parity_targets_the_right_agent(db_session, plan_env):
    task = make_task(db_session, plan_env, "dev", status="in_review", review_round=0)
    discussion = WorkerDiscussion(
        project_id=plan_env[1], task_id=task.id, source_work_id=1,
        review_kind="dev_review", subject="review_findings", review_round=0,
        owner_agent="author", reviewer_agent="reviewer", status="open",
        active_slot="active", turn=1, max_rounds=3, messages="[]",
    )
    db_session.add(discussion)
    db_session.commit()

    # Odd turn -> the reviewer holds the floor with the review kind.
    offer = planner.offer_for(db_session, plan_env[1], "task", task)
    assert (offer.kind, offer.target_agent, offer.discussion_id) == (
        "dev_review", "reviewer", discussion.id)

    # Even turn -> the original author replies with the base kind.
    discussion.turn = 2
    db_session.commit()
    offer = planner.offer_for(db_session, plan_env[1], "task", task)
    assert (offer.kind, offer.target_agent) == ("dev", "author")


# ---------------------------------------------------------------------------
# capability coverage
# ---------------------------------------------------------------------------

def test_coverage_is_unknown_without_a_mapped_worker(db_session, plan_env):
    # Fail open: nothing is known, so planning must not filter anything out.
    assert planner.covered_kinds(db_session, plan_env[1]) is None


def test_coverage_reflects_online_instances_only(db_session, plan_env):
    map_worker(db_session, plan_env, kinds=["dev", "qa_review"], online=False)
    # Mapped but nothing reachable: an empty set is the honest answer.
    assert planner.covered_kinds(db_session, plan_env[1]) == set()

    instance = db_session.query(AgentInstance).one()
    instance.online = True
    db_session.commit()
    assert planner.covered_kinds(db_session, plan_env[1]) == {"dev", "qa_review"}


def test_coverage_intersects_with_the_seven_work_kinds(db_session, plan_env):
    map_worker(db_session, plan_env, kinds=["dev", "unrelated-capability"])
    assert planner.covered_kinds(db_session, plan_env[1]) == {"dev"}


# ---------------------------------------------------------------------------
# planning pass
# ---------------------------------------------------------------------------

def test_plan_once_materialises_a_row_for_mapped_projects_only(client, db_session, plan_env):
    make_task(db_session, plan_env, "dev")
    # No mapping yet -> the Server must not plan this project at all.
    assert planner.plan_once(force=True) == 0

    map_worker(db_session, plan_env, kinds=["dev"])
    assert planner.plan_once(force=True) == 1

    db_session.expire_all()
    rows = db_session.query(WorkerWork).filter_by(project_id=plan_env[1]).all()
    assert [row.kind for row in rows] == ["dev"]
    # Re-planning is idempotent: the same business fingerprint must not fork rows.
    assert planner.plan_once(force=True) == 0
    assert db_session.query(WorkerWork).filter_by(project_id=plan_env[1]).count() == 1


def test_plan_once_skips_kinds_no_online_agent_advertises(client, db_session, plan_env):
    make_task(db_session, plan_env, "dev")
    make_task(db_session, plan_env, "qa")
    map_worker(db_session, plan_env, kinds=["dev"])

    assert planner.plan_once(force=True) == 1
    db_session.expire_all()
    rows = db_session.query(WorkerWork).filter_by(project_id=plan_env[1]).all()
    assert [row.kind for row in rows] == ["dev"]


def test_plan_once_closes_a_story_once_every_task_is_done(client, db_session, plan_env):
    map_worker(db_session, plan_env, kinds=["dev"])
    task = make_task(db_session, plan_env, "dev")
    assert planner.plan_once(force=True) == 1

    task.status = "done"
    db_session.commit()
    planner.plan_once(force=True)

    db_session.expire_all()
    assert db_session.get(Story, plan_env[2].id).status == "done"


# ---------------------------------------------------------------------------
# re-publishing available rows whose queue message was dropped
# ---------------------------------------------------------------------------

def published_row(db_session, env, *, age_minutes):
    from datetime import timedelta

    from agentboard.core.common.models import utc_now

    map_worker(db_session, env, kinds=["dev"])
    make_task(db_session, env, "dev")
    assert planner.plan_once(force=True) == 1
    row = db_session.query(WorkerWork).one()
    row.published_at = utc_now() - timedelta(minutes=age_minutes)
    db_session.commit()
    return row


def test_plan_once_republishes_available_rows_whose_message_was_dropped(client, db_session, plan_env):
    # Ack-and-drop (no eligible local Agent) leaves the row available but
    # published; the relay never re-publishes it on its own. Once coverage
    # is back, the planner must hand the row to the relay again.
    row = published_row(db_session, plan_env, age_minutes=4)

    assert planner.plan_once(force=True) == 0  # no new row: same work_key
    db_session.expire_all()
    assert db_session.query(WorkerWork).count() == 1
    assert db_session.get(WorkerWork, row.id).published_at is None


def test_plan_once_leaves_freshly_published_rows_alone(client, db_session, plan_env):
    row = published_row(db_session, plan_env, age_minutes=0)
    published_at = row.published_at

    planner.plan_once(force=True)

    db_session.expire_all()
    assert db_session.get(WorkerWork, row.id).published_at == published_at


def test_plan_once_does_not_republish_while_no_agent_is_online(client, db_session, plan_env):
    row = published_row(db_session, plan_env, age_minutes=4)
    db_session.query(AgentInstance).one().online = False
    db_session.commit()

    planner.plan_once(force=True)

    db_session.expire_all()
    assert db_session.get(WorkerWork, row.id).published_at is not None


def test_plan_once_does_not_touch_leased_rows(client, db_session, plan_env):
    from datetime import timedelta

    from agentboard.core.common.models import utc_now

    row = published_row(db_session, plan_env, age_minutes=4)
    row.state = "leased"
    row.lease_until = utc_now() + timedelta(minutes=3)
    db_session.commit()

    planner.plan_once(force=True)

    db_session.expire_all()
    assert db_session.get(WorkerWork, row.id).published_at is not None


# ---------------------------------------------------------------------------
# Server-side defaults moved out of the Worker
# ---------------------------------------------------------------------------

def test_default_ticket_plan_keeps_acceptance_out_of_development_tasks():
    plan = default_ticket_plan(
        "Greeting",
        "## Implementation\n- [ ] Build greeting\n## Acceptance\n- [ ] Default name",
    )
    assert [task["type"] for task in plan["tasks"]] == ["design", "dev", "qa"]
    assert len([task for task in plan["tasks"] if task["type"] == "dev"]) == 1
    design, dev, qa = (task["title"] for task in plan["tasks"])
    assert plan["dependencies"] == [[design, dev], [dev, qa]]


# ---------------------------------------------------------------------------
# project scope declaration (POST /api/worker-work/projects)
# ---------------------------------------------------------------------------

def declare_projects(client, db_session, uid, worker_id, project_ids):
    headers = auth_headers(login_token(client, db_session, uid))
    return client.post("/api/worker-work/projects", headers=headers,
                       json={"worker_id": worker_id, "project_ids": project_ids})


def add_mapped_project(db, uid, worker_id, project_id):
    if not db.query(Worker).filter_by(worker_id=worker_id).first():
        db.add(Worker(worker_id=worker_id, hostname="scope-host", status="active"))
    db.add(WorkerProjectMapping(worker_id=worker_id, project_id=project_id, enabled=True))
    db.commit()


def make_member_project(db, uid):
    from agentboard.features.projects.service import create_project

    project = create_project(db, name="scope project", key=f"S-{uuid.uuid4().hex[:6].upper()}")
    db.add(ProjectMember(project_id=project.id, user_id=uid, role="owner"))
    db.commit()
    return project.id


def test_declaration_requires_membership_in_listed_projects(client, db_session, plan_env):
    stranger_uid, _ = setup_user_project(db_session)
    add_mapped_project(db_session, plan_env[0], "w-scope", plan_env[1])

    response = declare_projects(client, db_session, stranger_uid, "w-scope", [plan_env[1]])

    assert response.status_code == 403


def test_declaration_disables_stale_and_reenables_listed(client, db_session, plan_env):
    uid, pid, _ = plan_env
    other_pid = make_member_project(db_session, uid)
    add_mapped_project(db_session, uid, "w-scope", pid)
    add_mapped_project(db_session, uid, "w-scope", other_pid)

    response = declare_projects(client, db_session, uid, "w-scope", [pid])

    assert response.status_code == 200
    assert response.json()["disabled"] == [other_pid]
    db_session.expire_all()
    mappings = {m.project_id: m.enabled for m in
                db_session.query(WorkerProjectMapping).filter_by(worker_id="w-scope")}
    assert mappings == {pid: True, other_pid: False}

    back = declare_projects(client, db_session, uid, "w-scope", [pid, other_pid])
    assert back.status_code == 200
    db_session.expire_all()
    assert db_session.query(WorkerProjectMapping).filter_by(
        worker_id="w-scope", project_id=other_pid).one().enabled is True


def test_declaration_cannot_disable_projects_the_caller_does_not_belong_to(
        client, db_session, plan_env):
    # Workers are shared across users: a member of project B must not be able
    # to shrink a Worker's scope for project A by omitting A from the list.
    uid, pid, _ = plan_env
    other_uid, other_pid = setup_user_project(db_session)
    add_mapped_project(db_session, uid, "w-shared", pid)
    add_mapped_project(db_session, other_uid, "w-shared", other_pid)

    response = declare_projects(client, db_session, other_uid, "w-shared", [other_pid])

    assert response.status_code == 200
    assert response.json()["disabled"] == []
    db_session.expire_all()
    assert db_session.query(WorkerProjectMapping).filter_by(
        worker_id="w-shared", project_id=pid).one().enabled is True


def test_declaration_accepts_empty_scope_and_disables_all_caller_projects(
        client, db_session, plan_env):
    uid, pid, _ = plan_env
    other_pid = make_member_project(db_session, uid)
    add_mapped_project(db_session, uid, "w-scope", pid)
    add_mapped_project(db_session, uid, "w-scope", other_pid)

    response = declare_projects(client, db_session, uid, "w-scope", [])

    assert response.status_code == 200
    assert sorted(response.json()["disabled"]) == sorted([pid, other_pid])
    db_session.expire_all()
    assert all(m.enabled is False for m in
               db_session.query(WorkerProjectMapping).filter_by(worker_id="w-scope"))


def test_empty_scope_still_cannot_touch_foreign_projects(client, db_session, plan_env):
    uid, pid, _ = plan_env
    other_uid, other_pid = setup_user_project(db_session)
    add_mapped_project(db_session, uid, "w-shared", pid)
    add_mapped_project(db_session, other_uid, "w-shared", other_pid)

    response = declare_projects(client, db_session, other_uid, "w-shared", [])

    assert response.status_code == 200
    assert response.json()["disabled"] == [other_pid]
    db_session.expire_all()
    assert db_session.query(WorkerProjectMapping).filter_by(
        worker_id="w-shared", project_id=pid).one().enabled is True


def test_declaration_unknown_worker_is_404(client, db_session, plan_env):
    response = declare_projects(client, db_session, plan_env[0], "w-missing", [plan_env[1]])
    assert response.status_code == 404
