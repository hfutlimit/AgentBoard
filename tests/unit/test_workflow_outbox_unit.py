"""Unit tests for the workflow outbox repository (Sprint 8 / 2026-08-28).

These tests pin the contract of ``OutboxRepository`` against a real
SQLite in-memory engine. They cover:

* add() writes rows that survive the surrounding transaction
* fetch_unpublished() orders oldest-first and respects max_attempts
* mark_published / mark_retry / mark_dead update the row correctly
* payload size cap is enforced

A separate e2e (``tests/test_workflow_outbox.py`` once landed in the
e2e/ tree) covers the publish + drain path end-to-end with a
fault-injecting publisher.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentboard.core.common.models import Base
from agentboard.core.infrastructure.outbox import (
    MAX_PAYLOAD_BYTES,
    OutboxRepository,
    WorkflowOutbox,
)


@pytest.fixture
def session():
    """Per-test in-memory SQLite session bound to a fresh schema."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_add_writes_row_visible_in_same_transaction(session):
    repo = OutboxRepository()
    row = repo.add(
        session,
        "task.reviewed",
        "task",
        42,
        ref_id=7,
        agent_id="worker-1",
    )
    # Caller is responsible for committing. Inside the same session
    # the row is visible via flush() (done in repo.add()).
    fetched = session.get(WorkflowOutbox, row.id)
    assert fetched is not None
    assert fetched.event == "task.reviewed"
    assert fetched.entity_type == "task"
    assert fetched.entity_id == 42
    assert fetched.ref_id == 7
    assert fetched.agent_id == "worker-1"
    assert fetched.published_at is None
    assert fetched.attempts == 0
    assert fetched.last_error is None


def test_add_round_trip_payload(session):
    repo = OutboxRepository()
    payload = {"k": "v", "n": 1, "nested": {"a": [1, 2, 3]}}
    row = repo.add(
        session,
        "task.reviewed",
        "task",
        1,
        payload=payload,
    )
    fetched = session.get(WorkflowOutbox, row.id)
    assert json.loads(fetched.payload_json) == payload


def test_add_rejects_oversize_payload(session):
    repo = OutboxRepository()
    huge = {"blob": "x" * (MAX_PAYLOAD_BYTES + 1)}
    with pytest.raises(ValueError, match="outbox payload too large"):
        repo.add(session, "task.reviewed", "task", 1, payload=huge)


def test_fetch_unpublished_orders_oldest_first(session):
    repo = OutboxRepository()
    ids = []
    for i in range(3):
        row = repo.add(session, "task.reviewed", "task", i)
        ids.append(row.id)
    session.commit()
    rows = repo.fetch_unpublished(session, limit=10)
    assert [r.id for r in rows] == ids  # insertion order == oldest first


def test_fetch_unpublished_excludes_published(session):
    repo = OutboxRepository()
    a = repo.add(session, "task.reviewed", "task", 1)
    b = repo.add(session, "task.reviewed", "task", 2)
    session.commit()
    repo.mark_published(session, a.id)
    session.commit()
    rows = repo.fetch_unpublished(session, limit=10)
    assert [r.id for r in rows] == [b.id]


def test_fetch_unpublished_respects_max_attempts(session):
    repo = OutboxRepository(max_attempts=3)
    a = repo.add(session, "task.reviewed", "task", 1)
    b = repo.add(session, "task.reviewed", "task", 2)
    session.commit()
    # a is retry-failed twice; b is fresh.
    repo.mark_retry(session, a.id, error="boom-1")
    repo.mark_retry(session, a.id, error="boom-2")
    # a.attempts == 2; max_attempts == 3, so a is still retryable
    rows = repo.fetch_unpublished(session, limit=10)
    assert [r.id for r in rows] == [a.id, b.id]
    # bump a to 3 (= max); now excluded from the live batch.
    repo.mark_retry(session, a.id, error="boom-3")
    rows = repo.fetch_unpublished(session, limit=10)
    assert [r.id for r in rows] == [b.id]
    # But it shows up in fetch_dead() so the operator can see it.
    dead = repo.fetch_dead(session, limit=10)
    assert [r.id for r in dead] == [a.id]


def test_mark_published_clears_last_error(session):
    repo = OutboxRepository()
    row = repo.add(session, "task.reviewed", "task", 1)
    session.commit()
    repo.mark_retry(session, row.id, error="transient")
    repo.mark_published(session, row.id)
    session.commit()
    fetched = session.get(WorkflowOutbox, row.id)
    assert fetched.published_at is not None
    assert fetched.last_error is None
    # attempts keeps history so operators can see "this row was
    # retried 1 time before success".
    assert fetched.attempts == 1


def test_mark_dead_skips_future_batches(session):
    repo = OutboxRepository(max_attempts=3)
    row = repo.add(session, "task.reviewed", "task", 1)
    session.commit()
    repo.mark_dead(session, row.id, error="poison: invalid payload")
    session.commit()
    # Live batch skips the dead row.
    rows = repo.fetch_unpublished(session, limit=10)
    assert rows == []
    # Dead batch includes it.
    dead = repo.fetch_dead(session, limit=10)
    assert [r.id for r in dead] == [row.id]


def test_retry_backoff_schedule_monotonic():
    """Schedule is monotonic; capped at the last entry so a runaway
    row does not back off for hours."""
    from agentboard.core.infrastructure.outbox import RETRY_BACKOFF_SECONDS
    schedule = list(RETRY_BACKOFF_SECONDS)
    assert schedule == sorted(schedule), "schedule must be monotonic"
    assert schedule[-1] <= 300, "cap the cap so a poison row does not back off for an hour"


def test_next_retry_delay_returns_a_finite_number():
    repo = OutboxRepository()
    for n in (0, 1, 2, 5, 99):
        d = repo.next_retry_delay(n)
        assert 0 <= d <= 300
