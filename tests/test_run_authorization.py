"""Integration coverage for project-scoped AgentRun mutation authorization."""
from __future__ import annotations

import os
import sys
import tempfile
import asyncio
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
from agentboard.features.identity.models import User  # noqa: E402
from agentboard.features.projects.models import Agent, Project, ProjectMember  # noqa: E402
from agentboard.features.scheduling.models import AgentRun, AgentSchedule  # noqa: E402


init_db()


@pytest.fixture(scope="module")
def seeded():
    with SessionLocal() as session:
        owner = User(username="run-auth-owner", password_hash="hash", is_admin=False)
        member = User(username="run-auth-member", password_hash="hash", is_admin=False)
        outsider = User(username="run-auth-outsider", password_hash="hash", is_admin=False)
        project = Project(name="Run auth project", key="RAP", description="")
        session.add_all([owner, member, outsider, project])
        session.flush()
        session.add_all([
            ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
            ProjectMember(project_id=project.id, user_id=member.id, role="member"),
        ])
        agent = Agent(
            agent_id="run-auth-agent",
            name="Run Auth Agent",
            user_id=owner.id,
            roles='["developer"]',
            capabilities="[]",
            model="test-model",
        )
        wrong_agent = Agent(
            agent_id="run-auth-wrong-agent",
            name="Wrong Agent",
            user_id=owner.id,
            roles='["developer"]',
            capabilities="[]",
            model="test-model",
        )
        session.add_all([agent, wrong_agent])
        session.flush()
        schedule = AgentSchedule(
            project_id=project.id,
            title="Run auth schedule",
            schedule_type="once",
            agent=agent.agent_id,
        )
        session.add(schedule)
        session.flush()
        run = AgentRun(
            schedule_id=schedule.id,
            agent_registry_id=agent.id,
            agent=agent.agent_id,
            model=agent.model,
            status="pending",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        _correct_key, correct_secret = service.create_api_key(
            session,
            user_id=owner.id,
            name="correct agent key",
            permissions=["api:read", "api:write"],
            agent_ref=agent.agent_id,
        )
        _wrong_key, wrong_secret = service.create_api_key(
            session,
            user_id=owner.id,
            name="wrong agent key",
            permissions=["api:read", "api:write"],
            agent_ref=wrong_agent.agent_id,
        )

        return {
            "run_id": run.id,
            "schedule_id": schedule.id,
            "owner": auth.make_token(owner.id),
            "member": auth.make_token(member.id),
            "outsider": auth.make_token(outsider.id),
            "correct_key": correct_secret,
            "wrong_key": wrong_secret,
        }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_project_member_can_read_but_cannot_mutate_run(seeded):
    client = TestClient(api.app)
    run_id = seeded["run_id"]

    assert client.get(f"/api/runs/{run_id}", headers=_headers(seeded["member"])).status_code == 200
    assert client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=_headers(seeded["member"]),
        json={"event_type": "agent.output", "payload": {"message": "blocked"}},
    ).status_code == 403
    assert client.patch(
        f"/api/runs/{run_id}",
        headers=_headers(seeded["member"]),
        json={"status": "running"},
    ).status_code == 403


def test_wrong_agent_key_is_rejected_and_correct_key_is_audited(seeded):
    client = TestClient(api.app)
    run_id = seeded["run_id"]

    wrong = client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=_headers(seeded["wrong_key"]),
        json={"event_type": "agent.output", "payload": {"message": "wrong"}},
    )
    assert wrong.status_code == 403

    correct = client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=_headers(seeded["correct_key"]),
        json={"event_type": "agent.output", "payload": {"message": "ok"}},
    )
    assert correct.status_code == 201, correct.text
    body = correct.json()
    assert body["api_key_id"] is not None
    assert body["agent_registry_id"] is not None
    assert body["actor_username_snapshot"] == "run-auth-owner"
    assert body["api_key_prefix_snapshot"]


def test_event_endpoint_requires_the_active_lease_worker(seeded):
    client = TestClient(api.app)
    run_id = seeded["run_id"]
    with SessionLocal() as session:
        run = session.get(AgentRun, run_id)
        run.lease_worker_id = "worker-a"
        run.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
        session.commit()
    try:
        owner_patch = client.patch(
            f"/api/runs/{run_id}",
            headers=_headers(seeded["owner"]),
            json={"status": "running"},
        )
        missing = client.post(
            f"/api/agent-runs/{run_id}/events",
            headers=_headers(seeded["correct_key"]),
            json={"event_type": "agent.output", "payload": {"message": "missing worker"}},
        )
        wrong = client.post(
            f"/api/agent-runs/{run_id}/events",
            headers={**_headers(seeded["correct_key"]), "X-Worker-ID": "worker-b"},
            json={"event_type": "agent.output", "payload": {"message": "wrong worker"}},
        )
        with SessionLocal() as session:
            run = session.get(AgentRun, run_id)
            run.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
            session.commit()
        expired = client.post(
            f"/api/agent-runs/{run_id}/events",
            headers={**_headers(seeded["correct_key"]), "X-Worker-ID": "worker-a"},
            json={"event_type": "agent.output", "payload": {"message": "expired"}},
        )
        with SessionLocal() as session:
            run = session.get(AgentRun, run_id)
            run.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
            session.commit()
        correct = client.post(
            f"/api/agent-runs/{run_id}/events",
            headers={**_headers(seeded["correct_key"]), "X-Worker-ID": "worker-a"},
            json={"event_type": "agent.output", "payload": {"message": "leased"}},
        )
        assert owner_patch.status_code == 403
        assert missing.status_code == 403
        assert wrong.status_code == 403
        assert expired.status_code == 403
        assert correct.status_code == 201, correct.text
        assert correct.json()["worker_id"] == "worker-a"
    finally:
        with SessionLocal() as session:
            run = session.get(AgentRun, run_id)
            run.lease_worker_id = None
            run.lease_expires_at = None
            session.commit()


def test_patch_cannot_roll_back_a_terminal_run(seeded):
    client = TestClient(api.app)
    with SessionLocal() as session:
        run = AgentRun(schedule_id=seeded["schedule_id"], status="pending")
        session.add(run)
        session.commit()
        run_id = run.id
    headers = _headers(seeded["owner"])
    assert client.patch(f"/api/runs/{run_id}", headers=headers, json={"status": "running"}).status_code == 200
    assert client.patch(f"/api/runs/{run_id}", headers=headers, json={"status": "success"}).status_code == 200
    rollback = client.patch(f"/api/runs/{run_id}", headers=headers, json={"status": "running"})
    assert rollback.status_code == 409, rollback.text


def test_outsider_cannot_open_run_stream(seeded):
    client = TestClient(api.app)
    response = client.get(
        f"/api/agent-runs/{seeded['run_id']}/events/stream",
        headers=_headers(seeded["outsider"]),
    )
    assert response.status_code == 403


def test_last_event_id_replays_only_newer_events(seeded):
    client = TestClient(api.app)
    run_id = seeded["run_id"]
    headers = _headers(seeded["correct_key"])
    first = client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=headers,
        json={"event_type": "agent.output", "payload": {"message": "first"}},
    ).json()
    second = client.post(
        f"/api/agent-runs/{run_id}/events",
        headers=headers,
        json={"event_type": "agent.output", "payload": {"message": "second"}},
    ).json()

    from agentboard.features.scheduling.router import stream_run_events

    class ReplayRequest:
        headers = {"last-event-id": str(first["id"])}

        async def is_disconnected(self):
            return True

    async def collect_replay():
        with SessionLocal() as session:
            response = await stream_run_events(
                run_id,
                ReplayRequest(),
                authorization=headers["Authorization"],
                s=session,
            )
            return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(collect_replay())

    text = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    assert f"id: {second['id']}\n" in text
    assert f"id: {first['id']}\n" not in text

    older = client.get(
        f"/api/agent-runs/{run_id}/events",
        headers=headers,
        params={"before_id": second["id"], "limit": 200},
    )
    assert older.status_code == 200, older.text
    older_ids = [event["id"] for event in older.json()]
    assert first["id"] in older_ids
    assert second["id"] not in older_ids
