from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

# The scheduling router exposes the shared agent_state_hub from api.py.  Load
# the application facade first so the split-router import is fully initialized.
from agentboard import api  # noqa: F401
from agentboard import api_helpers, service
from agentboard.features.scheduling.router import _event_to_wire, _format_sse


def test_run_event_payload_is_an_object_on_the_wire():
    event = SimpleNamespace(
        id=7,
        run_id=42,
        event_type="agent.output",
        payload='{"message":"working"}',
        actor_user_id=11,
        api_key_id=22,
        agent_registry_id=33,
        worker_id="worker-a",
        created_at=SimpleNamespace(isoformat=lambda: "2026-08-24T12:00:00"),
    )

    wire = _event_to_wire(event)
    assert wire["payload"] == {"message": "working"}
    assert wire["actor_user_id"] == 11
    assert wire["api_key_id"] == 22
    assert wire["agent_registry_id"] == 33
    assert wire["worker_id"] == "worker-a"
    sse = _format_sse(wire)
    assert '"payload": {"message": "working"}' in sse
    assert "id: 7\n" in sse
    assert "event: agent.output\n" in sse


def test_run_mutation_requires_the_bound_agent_identity(monkeypatch):
    run = SimpleNamespace(id=42, agent_registry_id=7)
    actor = api_helpers.ActorContext(
        user_id=11,
        is_admin=False,
        api_key_id=99,
        agent_registry_id=8,
    )
    monkeypatch.setattr(service, "get_run", lambda _s, _run_id: run)
    monkeypatch.setattr(api_helpers, "resolve_actor_context", lambda *_args, **_kwargs: actor)
    monkeypatch.setattr(api_helpers, "_auth_is_required", lambda: True)

    with pytest.raises(api_helpers.HTTPException) as exc_info:
        api_helpers._authorize_run_mutation(
            "Bearer test", SimpleNamespace(get=lambda *_args: None), 42,
            operation="event",
        )

    assert exc_info.value.status_code == 403


def test_run_mutation_accepts_matching_agent_identity(monkeypatch):
    run = SimpleNamespace(id=42, agent_registry_id=7)
    actor = api_helpers.ActorContext(
        user_id=11,
        is_admin=False,
        api_key_id=99,
        agent_registry_id=7,
    )
    monkeypatch.setattr(service, "get_run", lambda _s, _run_id: run)
    monkeypatch.setattr(api_helpers, "resolve_actor_context", lambda *_args, **_kwargs: actor)
    monkeypatch.setattr(api_helpers, "_auth_is_required", lambda: True)

    authorized_run, authorized_actor = api_helpers._authorize_run_mutation(
        "Bearer test", SimpleNamespace(get=lambda *_args: None), 42,
        operation="event",
    )

    assert authorized_run is run
    assert authorized_actor is actor


def test_run_mutation_honors_an_active_worker_lease(monkeypatch):
    run = SimpleNamespace(
        id=42,
        agent_registry_id=7,
        lease_worker_id="worker-a",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
    )
    actor = api_helpers.ActorContext(
        user_id=11,
        is_admin=False,
        api_key_id=99,
        agent_registry_id=7,
    )
    monkeypatch.setattr(service, "get_run", lambda _s, _run_id: run)
    monkeypatch.setattr(api_helpers, "resolve_actor_context", lambda *_args, **_kwargs: actor)
    monkeypatch.setattr(api_helpers, "_auth_is_required", lambda: True)
    session = SimpleNamespace(get=lambda *_args: None)

    with pytest.raises(api_helpers.HTTPException) as exc_info:
        api_helpers._authorize_run_mutation(
            "Bearer test", session, 42, operation="event",
        )
    assert exc_info.value.status_code == 403

    authorized_run, _ = api_helpers._authorize_run_mutation(
        "Bearer test", session, 42, operation="event", worker_id="worker-a",
    )
    assert authorized_run is run
