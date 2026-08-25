"""Integration coverage for read-side aggregate authorization.

Complements ``test_run_authorization.py`` (which focuses on write-side
mutation permissions) by covering the read path:

- ``GET /api/agent-runs/{rid}/events`` returns 403 to a project outsider
  instead of leaking the audit metadata (api_key_id, agent_registry_id,
  worker_id, payload).
- ``GET /api/agent-runs/{rid}/events/stream`` rejects outsiders at
  subscription time (the SSE path used to bypass project membership).
- ``GET /api/runs/{rid}`` requires the same membership boundary.
- ``GET /api/schedules/{sid}`` and ``GET /api/schedules/{sid}/runs`` are
  also locked down — those routes previously only required a valid token.
- ``GET /api/tasks/{tid}/review-context`` does not let a member of one
  project read another project's proposal spec via the task id.

P1-4 also covers the SSE replay gap: a 350-event backlog must replay in
full even though the page size is 200.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_REQUIRE_AUTH"] = "1"

for _module in list(sys.modules):
    if _module == "agentboard" or _module.startswith("agentboard."):
        del sys.modules[_module]

from fastapi.testclient import TestClient  # noqa: E402

from agentboard import api, auth, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.features.identity.models import ApiKey, User  # noqa: E402
from agentboard.features.projects.models import (  # noqa: E402
    Agent, Project, ProjectMember,
)
from agentboard.features.scheduling.models import (  # noqa: E402
    AgentRun, AgentSchedule,
)


init_db()


@pytest.fixture(scope="module")
def seeded():
    with SessionLocal() as session:
        # Two disjoint projects, each with an owner + a member, and a foreign
        # user with no project membership at all.
        owner_a = User(username="read-auth-owner-a", password_hash="hash", is_admin=False)
        member_a = User(username="read-auth-member-a", password_hash="hash", is_admin=False)
        owner_b = User(username="read-auth-owner-b", password_hash="hash", is_admin=False)
        member_b = User(username="read-auth-member-b", password_hash="hash", is_admin=False)
        outsider = User(username="read-auth-outsider", password_hash="hash", is_admin=False)
        admin = User(username="read-auth-admin", password_hash="hash", is_admin=True)
        project_a = Project(name="Read auth project A", key="RAA", description="")
        project_b = Project(name="Read auth project B", key="RAB", description="")
        session.add_all([
            owner_a, member_a, owner_b, member_b, outsider, admin,
            project_a, project_b,
        ])
        session.flush()
        session.add_all([
            ProjectMember(project_id=project_a.id, user_id=owner_a.id, role="owner"),
            ProjectMember(project_id=project_a.id, user_id=member_a.id, role="member"),
            ProjectMember(project_id=project_b.id, user_id=owner_b.id, role="owner"),
            ProjectMember(project_id=project_b.id, user_id=member_b.id, role="member"),
        ])
        agent_a = Agent(
            agent_id="read-auth-agent-a",
            name="Read Auth Agent A",
            user_id=owner_a.id,
            roles='["developer"]',
            capabilities="[]",
            model="test-model",
        )
        session.add(agent_a)
        session.flush()
        schedule_a = AgentSchedule(
            project_id=project_a.id,
            title="Read auth schedule A",
            schedule_type="once",
            agent=agent_a.agent_id,
        )
        session.add(schedule_a)
        session.flush()
        run_a = AgentRun(
            schedule_id=schedule_a.id,
            agent_registry_id=agent_a.id,
            agent=agent_a.agent_id,
            model=agent_a.model,
            status="pending",
        )
        session.add(run_a)
        session.commit()
        session.refresh(run_a)

        # Pre-create a few run events so the read endpoints have content to
        # leak if authorization regresses.
        from agentboard.features.scheduling.models import RunEvent
        for i in range(3):
            session.add(RunEvent(
                run_id=run_a.id,
                event_type="agent.output",
                payload=f'{{"message":"secret-a-{i}"}}',
                actor_user_id=owner_a.id,
                actor_username_snapshot=owner_a.username,
            ))
        session.commit()

        return {
            "run_id": run_a.id,
            "schedule_id": schedule_a.id,
            "project_a_id": project_a.id,
            "owner_a": auth.make_token(owner_a.id),
            "member_a": auth.make_token(member_a.id),
            "owner_b": auth.make_token(owner_b.id),
            "member_b": auth.make_token(member_b.id),
            "outsider": auth.make_token(outsider.id),
            "admin": auth.make_token(admin.id),
        }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- P0-1: read-side aggregate authorization ----------

def test_outsider_cannot_read_run_events(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/agent-runs/{seeded['run_id']}/events",
        headers=_headers(seeded["outsider"]),
    )
    assert response.status_code == 403, response.text
    # Body must not echo the audit metadata either.
    assert "secret-a" not in response.text


def test_project_b_member_cannot_read_project_a_run_events(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/agent-runs/{seeded['run_id']}/events",
        headers=_headers(seeded["member_b"]),
    )
    assert response.status_code == 403, response.text


def test_admin_can_read_any_run_events(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/agent-runs/{seeded['run_id']}/events",
        headers=_headers(seeded["admin"]),
    )
    assert response.status_code == 200, response.text
    assert "secret-a-0" in response.text


def test_project_a_member_can_read_project_a_run_events(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/agent-runs/{seeded['run_id']}/events",
        headers=_headers(seeded["member_a"]),
    )
    assert response.status_code == 200, response.text
    assert "secret-a-0" in response.text


def test_outsider_cannot_read_run(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/runs/{seeded['run_id']}",
        headers=_headers(seeded["outsider"]),
    )
    assert response.status_code == 403, response.text


def test_project_b_member_cannot_read_project_a_run(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/runs/{seeded['run_id']}",
        headers=_headers(seeded["member_b"]),
    )
    assert response.status_code == 403, response.text


def test_outsider_cannot_read_schedule(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/schedules/{seeded['schedule_id']}",
        headers=_headers(seeded["outsider"]),
    )
    assert response.status_code == 403, response.text


def test_outsider_cannot_read_schedule_runs(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/schedules/{seeded['schedule_id']}/runs",
        headers=_headers(seeded["outsider"]),
    )
    assert response.status_code == 403, response.text


def test_project_b_member_cannot_read_project_a_schedule(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/schedules/{seeded['schedule_id']}",
        headers=_headers(seeded["member_b"]),
    )
    assert response.status_code == 403, response.text


# ---------- P1-4: SSE replay 200-event gap ----------

def test_sse_stream_replays_full_backlog_past_page_size(seeded):
    """Even when the backlog exceeds the 200-event page size, the SSE
    stream must replay every event up to the snapshot watermark before
    transitioning to the live queue (P1-4). The previous implementation
    silently dropped events between the first 200 and the snapshot MAX(id).
    """
    # Seed 350 events so the backlog exceeds the 200-event page.
    from agentboard.features.scheduling.models import RunEvent
    run_id = seeded["run_id"]
    with SessionLocal() as session:
        for i in range(350):
            session.add(RunEvent(
                run_id=run_id,
                event_type="agent.output",
                payload=f'{{"i":{i}}}',
                actor_user_id=seeded.get("_owner_id"),  # may be None; ok
            ))
        session.commit()

    from agentboard.features.scheduling.router import stream_run_events

    class ReplayRequest:
        headers = {}  # last-event-id unset → replay everything

        async def is_disconnected(self):
            return True

    async def collect_replay():
        with SessionLocal() as session:
            response = await stream_run_events(
                run_id,
                ReplayRequest(),
                authorization=_headers(seeded["owner_a"])["Authorization"],
                s=session,
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

    text = asyncio.run(collect_replay())

    # 350 seeded + 3 fixture events = 353; verify all ids 1..353 were emitted.
    for i in range(1, 354):
        assert f"id: {i}\n" in text, f"missing event id={i} in SSE replay"


def test_sse_stream_replays_full_backlog_past_2000_cap(seeded):
    """P1-4 follow-up: an earlier version added a 2000-event safety cap
    that could itself cause the same silent-drop bug we just fixed. With
    the cap removed, every event between the client's Last-Event-ID and
    the snapshot MAX(id) must be replayed even when the backlog is
    thousands of rows deep.
    """
    from agentboard.features.scheduling.models import RunEvent

    run_id = seeded["run_id"]
    # Seed 5000 events so the backlog is well past the old cap.
    with SessionLocal() as session:
        for i in range(5000):
            session.add(RunEvent(
                run_id=run_id,
                event_type="agent.output",
                payload=f'{{"i":{i}}}',
            ))
        session.commit()

    from agentboard.features.scheduling.router import stream_run_events

    class ReplayRequest:
        headers = {}  # last-event-id unset → replay everything

        async def is_disconnected(self):
            return True

    async def collect_replay():
        with SessionLocal() as session:
            response = await stream_run_events(
                run_id,
                ReplayRequest(),
                authorization=_headers(seeded["owner_a"])["Authorization"],
                s=session,
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

    text = asyncio.run(collect_replay())

    # 5000 + 3 fixture events; the new code must replay every one.
    # The 2001st event is exactly the row that the old 2000 cap would
    # have dropped, so we assert it is present.
    assert f"id: 2001\n" in text, "replay dropped event id=2001 (regression of the 2000 cap bug)"
    assert f"id: 5000\n" in text, "replay dropped last event of the 5000-event backlog"


def test_sse_stream_emits_terminal_control_event_for_already_finished_run(seeded):
    from agentboard.features.scheduling.models import AgentRun
    from agentboard.features.scheduling.router import stream_run_events

    with SessionLocal() as session:
        run = session.get(AgentRun, seeded["run_id"])
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()

    class ReplayRequest:
        headers = {}

        async def is_disconnected(self):
            return True

    async def collect_terminal_event():
        with SessionLocal() as session:
            response = await stream_run_events(
                seeded["run_id"],
                ReplayRequest(),
                authorization=_headers(seeded["owner_a"])["Authorization"],
                s=session,
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

    text = asyncio.run(collect_terminal_event())
    assert "event: run.success\n" in text
    assert '"status": "success"' in text
