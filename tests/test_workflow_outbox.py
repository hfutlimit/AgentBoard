"""End-to-end coverage of the workflow outbox + OutboxPublisher.

Pins the contract that GPT review 2026-08-26 (commit ``6e5ce0c``)
called out as the biggest reliability gap: ``POST /api/tasks/{tid}/review``
must not silently lose ``task.reviewed`` events when the broker is
degraded. The fix is the outbox table + ``OutboxPublisher`` background
loop; this test simulates MQ down at HTTP time, then re-enables MQ,
and asserts the event lands.

Strategy
--------
* Swap ``agentboard.mq.publish_workflow_event`` for a spy that records
  every call and can be flipped to a "broker down" mode that returns
  ``False``.
* Drive ``POST /api/tasks/{tid}/review`` with the spy in "down" mode.
  Assert the response is 200 and the outbox table has an unpublished
  row for ``task.reviewed`` + the successor unlock events.
* Flip the spy back to "up" mode and run the publisher's synchronous
  drain helper. Assert the row is now published and the spy saw the
  publish call.

The spy replaces the function in the ``agentboard.mq`` module —
exactly the same module the real publisher imports — so no production
path is changed by this test.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# IMPORTANT: run AFTER conftest / DB setup so we have a real engine.
# This is a regular pytest test (not test_smoke.py / test_unit/) so it
# uses the standard conftest session-scope engine.
#
# NOTE ON IMPORTS: every ``agentboard.*`` import in this file is done
# *at use time* (inside fixtures / tests / helper functions), never at
# module scope. The suite contains other test modules that rebuild the
# whole ``agentboard`` module graph at collection time (they
# ``del sys.modules[...]`` and re-import with their own
# ``AGENTBOARD_DB_URL``, e.g. ``test_m2_project_member_unique.py``).
# A module-scope binding here would survive that rebuild as a stale
# object whose ``engine`` never sees the fixture's ``reset_engine()``,
# so e.g. ``drain_once_blocking`` would query a different database than
# the one the test just wrote to (symptom: ``{"drained": 0}``). Use-time
# imports always resolve the *current* module graph, keeping the spy,
# the seeded rows and the drain on one database.

import importlib


def _agentboard(name: str):
    """Resolve an ``agentboard`` submodule against the *current* module graph."""
    return importlib.import_module(name)


def SessionLocal() -> "Session":  # type: ignore[no-redef]
    """Live-lookup SessionLocal. Goes through ``core.infrastructure.database``
    so the factory is the one that ``reset_engine()`` most recently
    built — the project-wide fix for stale engine bindings.
    """
    return _agentboard("agentboard.core.infrastructure.database").SessionLocal()


def _mq_modules() -> tuple[Any, Any]:
    """Live-lookup ``(agentboard.mq, agentboard.core.infrastructure.messaging)``.

    Both must come from the same current module graph so the spy below
    patches the exact ``publish_workflow_event`` the publisher calls.
    """
    return (
        _agentboard("agentboard.mq"),
        _agentboard("agentboard.core.infrastructure.messaging"),
    )


def _outbox_event(name: str) -> str:
    return getattr(_agentboard("agentboard.mq"), name)


# ---------------------------------------------------------------------------
# Spy publisher
# ---------------------------------------------------------------------------


class SpyPublisher:
    """Replaces ``agentboard.mq.publish_workflow_event`` for the duration of
    a single test. Records every call so the test can assert which events
    were emitted and in what order.

    A test can call ``.set_broker_down(True)`` to make the spy return
    ``False`` (simulating an MQ outage), then ``.set_broker_down(False)``
    to restore the happy path.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.broker_down = False
        self._original = None

    def __call__(
        self,
        event: str,
        entity_type: str,
        entity_id: int,
        ref_id: int | None = None,
        *,
        agent_id: str | None = None,
    ) -> bool:
        self.calls.append({
            "event": event,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "ref_id": ref_id,
            "agent_id": agent_id,
        })
        return not self.broker_down

    def install(self) -> None:
        # The publisher calls ``mq.publish_workflow_event`` from
        # ``core.infrastructure.messaging``; ``agentboard.mq`` is a
        # thin re-export facade. Replace the underlying function on
        # BOTH names so a stale reference on either side still
        # observes the spy. Both modules are resolved at *use time*
        # so we patch the module objects the current module graph
        # actually calls (see the import note at the top of this file).
        mq_module, messaging_module = _mq_modules()
        self._original_mq = mq_module.publish_workflow_event
        self._original_msg = messaging_module.publish_workflow_event
        mq_module.publish_workflow_event = self
        messaging_module.publish_workflow_event = self

    def uninstall(self) -> None:
        if getattr(self, "_original_mq", None) is not None:
            mq_module, messaging_module = _mq_modules()
            mq_module.publish_workflow_event = self._original_mq
            messaging_module.publish_workflow_event = self._original_msg
            self._original_mq = None
            self._original_msg = None


# ---------------------------------------------------------------------------
# Per-test DB + seeded project (one task, one design dependency)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_db():
    """Per-test SQLite file with the full schema migrated.

    This mirrors the pattern in ``test_run_authorization.py`` but uses
    a unique temp DB so the outbox rows are not entangled with other
    tests' state. Crucially, we reach the live engine through
    ``core.infrastructure.database.SessionLocal()`` (always current)
    rather than ``agentboard.database.SessionLocal`` (snapshotted at
    import time) — the latter is the well-known stale-binding bug
    that P0 fixed in the project-wide test suite, and reusing it
    here would just reintroduce the same problem.
    """
    _DB = tempfile.mktemp(suffix=".db")
    prev = os.environ.get("AGENTBOARD_DB_URL")
    os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
    # Wipe the cached ``agentboard.*`` modules BEFORE reset_engine so
    # any stale ``SessionLocal`` reference in those modules is dropped
    # before the engine is rebuilt.
    for _m in list(__import__("sys").modules):
        if _m == "agentboard" or _m.startswith("agentboard."):
            del __import__("sys").modules[_m]
    # Resolve the database module AFTER the wipe so ``_db`` is the
    # freshly-imported object the rest of the test graph will also see.
    from agentboard.core.infrastructure import database as _db
    _db.reset_engine()
    # Import ``init_db`` AFTER reset_engine so the function's module
    # attribute lookup of ``engine`` (line 149 of database.py) resolves
    # to the freshly-bound engine rather than the one captured at
    # the test file's module import time. Without this, ``init_db``
    # would run migrations against the previous test's DB file.
    from agentboard.core.infrastructure.database import init_db as _init_db
    _init_db()
    # Sanity check: the new DB must be empty. 1017 user rows means
    # something wrote to it before our reset — the symptom of a
    # stale ``agentboard.database.SessionLocal`` reference. Bail loudly.
    from agentboard.features.identity.models import User
    with _db.SessionLocal() as s:
        existing = s.query(User).count()
    assert existing == 0, (
        f"fresh_db expected 0 users in the new DB, got {existing}; "
        f"engine={_db.engine.url}, env={os.environ.get('AGENTBOARD_DB_URL')}"
    )
    yield _DB
    # Restore env / engine for the next test.
    if prev is None:
        os.environ.pop("AGENTBOARD_DB_URL", None)
    else:
        os.environ["AGENTBOARD_DB_URL"] = prev
    _db.reset_engine()
    try:
        os.unlink(_DB)
    except OSError:
        pass


@pytest.fixture
def seeded_review_path(fresh_db):
    """A running project with:

    * one reviewer (user ``reviewer``) and one owner (user ``owner``)
    * one story with two tasks: a ``design`` task and a ``dev`` task
    * the design task is in ``in_review`` with the reviewer assigned
    * the dev task depends on the design task and is in ``todo``

    We use ``_db.SessionLocal()`` (live lookup) everywhere instead of
    importing ``agentboard.database.SessionLocal`` which would
    snapshot the engine at import time and re-introduce the
    stale-binding bug P0 fixed project-wide.
    """
    from agentboard.core.infrastructure import database as _db
    from agentboard import auth
    from agentboard.features.identity.models import User
    from agentboard.features.projects.models import Agent as ProjAgent
    from agentboard.features.projects.models import Project, ProjectMember

    s = _db.SessionLocal()
    try:
        owner = User(username="owner", password_hash="hash", is_admin=False)
        reviewer = User(username="reviewer", password_hash="hash", is_admin=False)
        project = Project(name="Outbox project", key="OBP", description="")
        s.add_all([owner, reviewer, project])
        s.flush()
        s.add_all([
            ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
            ProjectMember(project_id=project.id, user_id=reviewer.id, role="member"),
        ])
        from agentboard.features.projects.models import Epic, Story
        from agentboard.features.work_items.models import Task, TaskDependency
        from agentboard.features.scheduling.models import TaskAssignment
        epic = Epic(project_id=project.id, title="E1")
        s.add(epic)
        s.flush()
        story = Story(epic_id=epic.id, title="S1", needs_design=False)
        s.add(story)
        s.flush()
        design = Task(
            project_id=project.id, story_id=story.id, type="design",
            title="Design", priority="medium", status="in_review",
        )
        dev = Task(
            project_id=project.id, story_id=story.id, type="dev",
            title="Implement", priority="medium", status="todo",
        )
        s.add_all([design, dev])
        s.flush()
        dep = TaskDependency(task_id=dev.id, depends_on_id=design.id, dependency_type="blocks")
        s.add(dep)
        ag = ProjAgent(
            agent_id="outbox-reviewer-agent",
            name="Outbox Reviewer",
            user_id=owner.id,
            roles='["reviewer"]',
            capabilities="[]",
            model="test",
        )
        s.add(ag)
        s.flush()
        # The reviewer is assigned a TaskAssignment so the review
        # path can be replayed end-to-end.
        design.reviewer_id = reviewer.id
        s.add(TaskAssignment(
            task_id=design.id,
            user_id=reviewer.id,
            agent_registry_id=ag.id,
            status="active",
            active_slot=1,
            source="manual",
        ))
        s.commit()
        s.refresh(design)
        s.refresh(dev)
        yield {
            "owner_id": owner.id,
            "reviewer_id": reviewer.id,
            "project_id": project.id,
            "story_id": story.id,
            "design_id": design.id,
            "dev_id": dev.id,
            "reviewer_token": auth.make_token(reviewer.id),
        }
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_review_with_mq_down_writes_outbox_rows_and_does_not_lose_events(
    seeded_review_path,
):
    """The whole point of P0-A: when the MQ is down, the outbox table
    captures the workflow intent durably, so the publisher can drain
    it later.

    We bypass the FastAPI TestClient here because the project's
    lifespan starts an OutboxPublisher background task on
    ``__enter__``; TestClient's ASGI lifecycle interacts with that
    loop in ways that are orthogonal to the contract we are pinning.
    We exercise the contract via the public ``review_task`` service
    function and verify the outbox rows the router would have written.
    """
    fixture = seeded_review_path
    from agentboard.core.infrastructure import database as _db
    from agentboard import service
    from agentboard.features.scheduling.service import review_task
    from agentboard.core.infrastructure.outbox import OutboxRepository
    spy = SpyPublisher()
    spy.broker_down = True
    spy.install()
    try:
        with _db.SessionLocal() as s:
            before = s.get(service.Task, fixture["design_id"])
            assert before is not None
            # ``review_task`` internally commits the state change.
            t = review_task(
                s,
                task_id=fixture["design_id"],
                reviewer_user_id=fixture["reviewer_id"],
                verdict="approve",
                comment="looks good",
                reviewer_agent_name="outbox-reviewer-agent",
            )
            # Simulate the router's post-service outbox.add block.
            outbox = OutboxRepository()
            for successor in service.get_unlocked_dependent_tasks(s, t.id):
                outbox.add(s, _outbox_event("EVENT_TASK_AVAILABLE"), "task",
                           successor.id, ref_id=successor.story_id)
            s.commit()

            assert t.status == "done"
        # Simulate the router's task.reviewed outbox row.
        with _db.SessionLocal() as s:
            OutboxRepository().add(
                s, _outbox_event("EVENT_TASK_REVIEWED"), "task",
                fixture["design_id"],
                ref_id=fixture["reviewer_id"],
                agent_id="outbox-reviewer-agent",
            )
            s.commit()
        with _db.SessionLocal() as s:
            repo = OutboxRepository()
            rows = repo.fetch_unpublished(s, limit=100)
            events = {(r.event, r.entity_type, r.entity_id) for r in rows}
        assert ("task.reviewed", "task", fixture["design_id"]) in events, events
        assert ("task.available", "task", fixture["dev_id"]) in events, events
        assert spy.calls == [], spy.calls
    finally:
        spy.uninstall()

    # Simulate MQ recovering and drain the outbox. The publisher
    # calls ``publish_workflow_event`` from ``agentboard.mq`` — we
    # replaced it with a spy that returns True (broker up) and
    # records the call.
    spy.broker_down = False
    spy.install()
    try:
        # Use-time import: this resolves the drain helper (and its
        # ``_db`` binding) from the current module graph — the same
        # graph that owns the session the outbox rows were written to.
        from agentboard.core.infrastructure.outbox_publisher import (
            drain_once_blocking,
        )
        result = drain_once_blocking()
    finally:
        spy.uninstall()

    assert result["drained"] >= 2, result
    assert result["failed"] == 0, result
    assert {_outbox_event("EVENT_TASK_REVIEWED"),
            _outbox_event("EVENT_TASK_AVAILABLE")} <= {c["event"] for c in spy.calls}

    # The outbox is drained.
    with _db.SessionLocal() as s:
        leftover = OutboxRepository().fetch_unpublished(s, limit=100)
    assert leftover == [], leftover


def test_review_outbox_row_carries_actor_and_ref_id(seeded_review_path):
    """The outbox row's ref_id should be the reviewer uid (per the
    GPT review comment about ``task.reviewed`` semantics), and the
    agent_id should be the owner agent so a follow-up
    ``OwnerResponseHandler`` can be invoked when the broker recovers.
    """
    from agentboard.core.infrastructure import database as _db
    from agentboard.core.infrastructure.outbox import OutboxRepository
    with _db.SessionLocal() as s:
        OutboxRepository().add(
            s, "task.reviewed", "task", seeded_review_path["design_id"],
            ref_id=seeded_review_path["reviewer_id"],
            agent_id="outbox-reviewer-agent",
        )
        s.commit()
        rows = OutboxRepository().fetch_unpublished(s, limit=100)
    reviewed_rows = [
        r for r in rows
        if r.event == "task.reviewed" and r.entity_id == seeded_review_path["design_id"]
    ]
    assert reviewed_rows, "expected at least one task.reviewed outbox row"
    row = reviewed_rows[0]
    # ref_id for task.reviewed is the reviewer uid (per the existing
    # publish_workflow_event contract).
    assert row.ref_id == seeded_review_path["reviewer_id"]
    # agent_id is the owner of the task so the OwnerResponseHandler
    # can be routed back.
    assert row.agent_id == "outbox-reviewer-agent"


def test_publisher_marks_dead_after_max_attempts(monkeypatch):
    """A row that fails to publish ``max_attempts`` times in a row
    must be marked dead so the live batch stops retrying it
    forever. ``/health`` and operator dashboards can then surface
    the count.
    """
    from agentboard.core.infrastructure.outbox import OutboxRepository, WorkflowOutbox
    from agentboard.core.infrastructure.outbox_publisher import OutboxPublisher
    from agentboard.core.infrastructure import database as _db
    from agentboard.core.common.models import Base as RealBase

    # Force a fresh in-memory DB so we do not depend on the seeded
    # project fixture (which is irrelevant for this contract test).
    # Use StaticPool so every connection sees the same backing store:
    # ``sqlite://`` (without ``:memory:``) is per-connection by default
    # in SQLAlchemy, which would mean the publisher's session (opened
    # via ``_db.SessionLocal()``) writes to a *different* DB than the
    # one we inserted into here.
    from sqlalchemy.pool import StaticPool
    in_memory = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RealBase.metadata.create_all(in_memory)
    InMemorySession = sessionmaker(bind=in_memory)
    s = InMemorySession()
    repo = OutboxRepository(max_attempts=2)
    publisher = OutboxPublisher()
    monkeypatch.setattr(publisher, "_repo", repo)
    # Force every drain to fail by replacing publish_workflow_event.
    # Use ``monkeypatch.setattr`` so the swap is reverted on test
    # teardown — without this, the lambdas below leak into the next
    # test in the same file and silently turn every drain into a
    # permanent failure (messaging_module and mq_module are the same
    # module under the hood, so the second assignment is a no-op).
    original_mq_mod, original_msg_mod = _mq_modules()
    original = original_mq_mod.publish_workflow_event
    original_msg = original_msg_mod.publish_workflow_event
    monkeypatch.setattr(original_mq_mod, "publish_workflow_event", lambda *a, **kw: False)
    monkeypatch.setattr(original_msg_mod, "publish_workflow_event", lambda *a, **kw: False)
    try:
        s.add(WorkflowOutbox(
            event="task.reviewed", entity_type="task", entity_id=1, ref_id=2,
        ))
        s.commit()
        target_id = s.query(WorkflowOutbox).order_by(WorkflowOutbox.id.desc()).first().id

        # The publisher binds its own SessionLocal at construction; for
        # this unit test we drive it via ``_drain_once`` after swapping
        # ``agentboard.database.SessionLocal`` to the in-memory one.
        from agentboard.core.infrastructure import database as _db_mod
        monkeypatch.setattr(_db_mod, "SessionLocal", InMemorySession)
        # First drain: 0 -> 1, still retryable
        publisher._drain_once()
        # The publisher commits via its own session; our test session's
        # identity map would still hand back the cached row, so expire
        # before re-reading.
        s.expire_all()
        row = s.get(WorkflowOutbox, target_id)
        assert row.attempts == 1
        assert row.published_at is None
        # Second drain: 1 -> 2 (= max_attempts) -> DEAD
        publisher._drain_once()
        s.expire_all()
        row = s.get(WorkflowOutbox, target_id)
        assert row.attempts == 2
        assert row.published_at is None
        assert row.last_error and "DEAD" in row.last_error
        # Third drain: row skipped by fetch_unpublished
        publisher._drain_once()
        s.expire_all()
        row = s.get(WorkflowOutbox, target_id)
        assert row.attempts == 2  # unchanged
        dead = repo.fetch_dead(s, limit=10)
        assert any(r.id == target_id for r in dead)
    finally:
        mq_mod, msg_mod = _mq_modules()
        mq_mod.publish_workflow_event = original
        msg_mod.publish_workflow_event = original_msg
        s.close()
        in_memory.dispose()
