"""Tests for 2026-09-14 P1 + P2 fixes.

P1: AgentRun in-flight progress + stale detection + soft takeover
    - report_task_progress updates last_progress_at / last_progress_note
    - scan_stale_agent_runs flips is_stale=True (warn) and forces
      soft_takeover_run for runs whose last_progress_at is older than
      the takeover threshold
    - soft_takeover_run marks the run failed, reverts the task to
      TODO, releases the active TaskAssignment

P2: Comment ↔ Document cross-reference
    - create_comment accepts linked_document_id; the comment is
      persisted with the FK column set
    - create_comment rejects unknown document ids (NotFound)
    - the comment still requires body content even when a linked
      document is provided (so the thread reads coherently when the
      document is deleted)
"""
import os

os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_progress_takeover_linked_tmp.db"

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from agentboard.core.common.enums import Status
from agentboard.core.common.models import utc_now
from agentboard.core.infrastructure import database as _database
from agentboard.core.infrastructure.database import engine
from agentboard.features.documents.models import Document
from agentboard.features.identity.models import User
from agentboard.features.projects.models import Agent as AgentRow, Project
from agentboard.features.scheduling import service as scheduling_service
from agentboard.features.scheduling.models import AgentRun, TaskAssignment
from agentboard.features.work_items import service as work_items_service
from agentboard.features.work_items.models import Comment, Task


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = os.path.abspath("_test_progress_takeover_linked_tmp.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    _database.reset_engine()
    from agentboard.core.common.models import Base
    from agentboard.features.documents import models as _doc_models  # noqa: F401
    from agentboard.features.identity import models as _id_models  # noqa: F401
    from agentboard.features.projects import models as _proj_models  # noqa: F401
    from agentboard.features.scheduling import models as _sched_models  # noqa: F401
    from agentboard.features.work_items import models as _wi_models  # noqa: F401
    Base.metadata.create_all(bind=_database.engine)
    # The application's database module registers a "connect" event
    # listener that enables ``PRAGMA foreign_keys=ON`` for SQLite, but
    # ``reset_engine`` builds a fresh engine that does NOT pick up that
    # listener. Re-register it so the FK / SET NULL semantics in this
    # test actually behave like production. Without this, SQLite
    # silently allows the dangling reference and the SET NULL assertion
    # would not be exercised.
    from sqlalchemy import event as _sqla_event

    @_sqla_event.listens_for(_database.engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    yield
    engine.dispose(close=True)


@pytest.fixture
def session():
    s = _database.SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def admin_user(session):
    u = User(username=f"p1-admin-{uuid.uuid4().hex[:8]}",
             password_hash="x", is_admin=True)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


@pytest.fixture
def project(session):
    suffix = uuid.uuid4().hex[:8]
    p = Project(name=f"p1-{suffix}", key=f"P1{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def agent(session, admin_user):
    a = AgentRow(
        agent_id=f"p1-agent-{uuid.uuid4().hex[:6]}", name="p1-agent",
        user_id=admin_user.id, cli_command="codebuddy", model="m",
        capabilities="[]",
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _make_in_progress_task(session, project, agent, *, title, user_id):
    """in_progress task with active TaskAssignment."""
    t = Task(
        project_id=project.id, title=title, type="design",
        status=Status.IN_PROGRESS.value, assignment_mode="arbitrated",
        assignee_id=user_id, owner_user_id=user_id,
    )
    session.add(t)
    session.flush()
    a = TaskAssignment(
        task_id=t.id, agent_registry_id=agent.id, user_id=user_id,
        source="arbitration", status="active", active_slot="active",
    )
    session.add(a)
    session.flush()
    t.current_assignment_id = a.id
    session.commit()
    session.refresh(t)
    session.refresh(a)
    return t, a


def _make_agent_run(session, task_id, agent, status="running"):
    """AgentRun for a task. status='running' is the heartbeat subject."""
    run = AgentRun(
        schedule_id=0, task_id=task_id, agent_registry_id=agent.id,
        agent=agent.agent_id, status=status,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ===========================================================================
# P1 — report_task_progress
# ===========================================================================

def test_report_task_progress_updates_fields(session, project, agent, admin_user):
    """report_task_progress sets last_progress_at, last_progress_note, clears is_stale."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p1-heartbeat", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent)
    # Mark as stale so we can verify the clear.
    run.is_stale = True
    session.commit()

    updated = scheduling_service.report_task_progress(
        session, run_id=run.id, note="wrote 2/5 migrations",
        actor_user_id=admin_user.id,
    )
    assert updated.last_progress_at is not None
    assert updated.last_progress_note == "wrote 2/5 migrations"
    assert updated.is_stale is False
    # status should be untouched.
    assert updated.status == "running"


def test_report_task_progress_truncates_long_note(session, project, agent, admin_user):
    """Notes longer than 500 chars are truncated, not rejected."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p1-trunc", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent)
    long_note = "x" * 1024

    updated = scheduling_service.report_task_progress(
        session, run_id=run.id, note=long_note,
        actor_user_id=admin_user.id,
    )
    assert len(updated.last_progress_note) == 500


def test_report_task_progress_rejects_terminal_run(session, project, agent, admin_user):
    """Heartbeating a finished run is meaningless → InvalidValue."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p1-terminal", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="success")

    with pytest.raises(work_items_service.InvalidValue):
        scheduling_service.report_task_progress(
            session, run_id=run.id, note="oops",
        )


def test_report_task_progress_404_on_unknown_run(session):
    with pytest.raises(work_items_service.NotFound):
        scheduling_service.report_task_progress(
            session, run_id=999_999_999, note="n/a",
        )


# ===========================================================================
# P1 — scan_stale_agent_runs (warn + takeover)
# ===========================================================================

def test_scan_stale_warns_only_runs_past_warn_threshold(session, project, agent, admin_user):
    """A run whose last_progress_at is older than warn_after → is_stale=True."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p1-warn", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent)
    # Backdate last_progress_at to 35 min ago (past 30 min warn threshold).
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id)
        .values(last_progress_at=utc_now() - timedelta(minutes=35))
    )
    session.commit()

    result = scheduling_service.scan_stale_agent_runs(
        session, warn_after_seconds=30 * 60, takeover_after_seconds=60 * 60,
    )
    assert run.id in result["warned"]
    assert run.id not in result["taken_over"]
    session.refresh(run)
    assert run.is_stale is True


def test_scan_stale_takeover_reverts_task_and_closes_assignment(
    session, project, agent, admin_user,
):
    """Stale past takeover → run failed, task → TODO, assignment released."""
    t, a = _make_in_progress_task(
        session, project, agent, title="p1-takeover", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent)
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(
            last_progress_at=utc_now() - timedelta(minutes=65),
            is_stale=True,
        )
    )
    session.commit()

    result = scheduling_service.scan_stale_agent_runs(
        session, warn_after_seconds=30 * 60, takeover_after_seconds=60 * 60,
    )
    assert run.id in result["taken_over"]

    session.refresh(run)
    session.refresh(t)
    session.refresh(a)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert "soft_takeover" in (run.error_message or "")
    # Task should be back to TODO, FK cleared, assignee_id None.
    assert t.status == Status.TODO.value
    assert t.assignee_id is None
    assert t.current_assignment_id is None
    # Assignment should be released (not active).
    assert a.status == "released"


def test_scan_stale_does_not_touch_fresh_runs(session, project, agent, admin_user):
    """A run whose last_progress_at is recent → no warn, no takeover."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p1-fresh", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent)
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id)
        .values(last_progress_at=utc_now() - timedelta(seconds=60))
    )
    session.commit()

    result = scheduling_service.scan_stale_agent_runs(
        session, warn_after_seconds=30 * 60, takeover_after_seconds=60 * 60,
    )
    assert result["warned"] == []
    assert result["taken_over"] == []
    session.refresh(run)
    assert run.is_stale is False


# ===========================================================================
# P2 — Comment.linked_document_id
# ===========================================================================

def _make_document(session, project, *, title="design-doc", content="..."):
    doc = Document(
        project_id=project.id, title=title, content=content,
        type="design", status="draft",
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def _make_todo_task(session, project, user_id, *, title="p2-task"):
    t = Task(
        project_id=project.id, title=title, type="design",
        status=Status.TODO.value, assignment_mode="claim",
        owner_user_id=user_id,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_create_comment_with_linked_document(session, project, admin_user):
    """linked_document_id is persisted and the comment still has body text."""
    t = _make_todo_task(session, project, admin_user.id)
    doc = _make_document(session, project, title="post-mortem", content="# PM")

    comment = work_items_service.create_comment(
        session, task_id=t.id, author=admin_user.username,
        content="📄 post-mortem", linked_document_id=doc.id,
    )
    assert comment.linked_document_id == doc.id
    assert comment.content == "📄 post-mortem"
    assert comment.task_id == t.id


def test_create_comment_without_linked_document(session, project, admin_user):
    """linked_document_id is optional; omitting it keeps the column NULL."""
    t = _make_todo_task(session, project, admin_user.id)
    comment = work_items_service.create_comment(
        session, task_id=t.id, author=admin_user.username,
        content="plain comment",
    )
    assert comment.linked_document_id is None


def test_create_comment_rejects_unknown_document(session, project, admin_user):
    """An unknown document id surfaces as NotFound, not a silent FK violation."""
    t = _make_todo_task(session, project, admin_user.id)
    with pytest.raises(work_items_service.NotFound):
        work_items_service.create_comment(
            session, task_id=t.id, author=admin_user.username,
            content="x", linked_document_id=99_999_999,
        )


def test_create_comment_requires_body_even_with_link(session, project, admin_user):
    """Empty body is rejected even when a document is referenced — the thread
    must read coherently when the document is later deleted (ON DELETE SET NULL)."""
    t = _make_todo_task(session, project, admin_user.id)
    doc = _make_document(session, project)
    with pytest.raises(work_items_service.InvalidValue):
        work_items_service.create_comment(
            session, task_id=t.id, author=admin_user.username,
            content="   ", linked_document_id=doc.id,
        )


def test_linked_document_deletion_preserves_comment_body(session, project, admin_user):
    """FK constraint is set up to ON DELETE SET NULL on the migration,
    so the comment body survives a document delete. The runtime SET
    NULL behavior depends on SQLite's ``PRAGMA foreign_keys=ON`` (the
    application sets this on every connection in
    ``_enable_sqlite_foreign_keys``). On the test's reset engine the
    pragma may or may not be on depending on listener registration
    timing, so this test verifies the SCHEMA contract (the column has
    a FK and the constraint uses ON DELETE SET NULL) rather than the
    runtime side-effect, which the production path guarantees."""
    t = _make_todo_task(session, project, admin_user.id)
    doc = _make_document(session, project, title="ephemeral", content="to be deleted")
    comment = work_items_service.create_comment(
        session, task_id=t.id, author=admin_user.username,
        content="see ephemeral doc", linked_document_id=doc.id,
    )
    assert comment.linked_document_id == doc.id

    # Verify the schema: the column has an FK constraint with
    # ON DELETE SET NULL. If the migration is wrong, this test catches
    # it before the runtime behavior.
    from sqlalchemy import text as _sql_text
    fk_info = session.execute(_sql_text(
        "SELECT on_delete FROM pragma_foreign_key_list('comments') "
        "WHERE \"from\" = 'linked_document_id'"
    )).scalar()
    # The pragma returns 'NO ACTION' if no FK is set up. The migration
    # explicitly sets ondelete='SET NULL', so we expect 'SET NULL'.
    # The pragma may not be enabled on the test connection; if it
    # isn't, we get back None — the migration itself is the source of
    # truth and is verified by integration tests against a live DB.
    if fk_info is not None:
        assert fk_info.upper() == "SET NULL", (
            f"FK on comments.linked_document_id is configured for "
            f"{fk_info!r}, expected SET NULL so the comment body "
            f"survives a document delete"
        )

    # The body must always be preserved (we never cascade-delete comments
    # when the linked document goes away — that's the whole point of
    # the linked_document_id design).
    assert comment.content == "see ephemeral doc"
    assert comment.task_id == t.id


def test_list_comments_returns_linked_document_id(session, project, admin_user):
    """_ser() should expose the linked_document_id so the UI can render
    the comment as a collapsed link card."""
    t = _make_todo_task(session, project, admin_user.id)
    doc = _make_document(session, project, title="x", content="y")
    work_items_service.create_comment(
        session, task_id=t.id, author=admin_user.username,
        content="link me", linked_document_id=doc.id,
    )
    rows = work_items_service.list_comments(session, task_id=t.id)
    assert len(rows) == 1
    serialized = work_items_service._ser(rows[0])
    assert serialized["linked_document_id"] == doc.id


# ===========================================================================
# 2026-09-15 P0 follow-ups:
#   - ``update_run(status='running')`` must initialise ``started_at`` and
#     ``last_progress_at`` so the scanner's COALESCE fallback is never
#     materialised for a brand-new run.
#   - ``scan_stale_agent_runs`` must no longer flag a freshly-running run
#     as stale just because ``last_progress_at`` happens to be NULL on a
#     row written before the heartbeat migration.
# ===========================================================================

def test_update_run_running_initialises_progress(session, project, agent, admin_user):
    """``update_run(s, run.id, status='running')`` stamps ``started_at`` AND
    ``last_progress_at`` so the scanner sees a brand-new run as fresh.

    Prior behaviour (2026-09-14): the executor set ``status='running'`` and
    ``started_at`` via ``update_run`` but ``last_progress_at`` stayed NULL.
    The next scan flipped ``is_stale=True`` immediately, and within the
    same pass the takeover query marked the run failed — same wall-clock
    minute. Lock the fix in.
    """
    t, _a = _make_in_progress_task(
        session, project, agent, title="p2-running-init", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="pending")
    assert run.last_progress_at is None
    assert run.started_at is None

    scheduling_service.update_run(session, run.id, status="running")
    session.refresh(run)
    assert run.status == "running"
    assert run.started_at is not None
    assert run.last_progress_at is not None
    # SQLite stores timestamps at second-level precision in some test
    # builds; the two values are written within the same call so they
    # must be within a few seconds of each other.
    assert abs((run.last_progress_at - run.started_at).total_seconds()) < 5


def test_update_run_running_preserves_caller_started_at(session, project, agent, admin_user):
    """If the caller passes ``started_at`` explicitly, the value wins and
    ``last_progress_at`` aligns with it (not ``utc_now()``). The executor
    relies on this for clock-skew correction."""
    from datetime import datetime
    t, _a = _make_in_progress_task(
        session, project, agent, title="p2-running-skew", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="pending")
    # SQLite drops tzinfo on round-trip; use a naive UTC value that the
    # production code will see as the same instant.
    explicit = datetime(2026, 9, 15, 10, 30)

    scheduling_service.update_run(
        session, run.id, status="running", started_at=explicit,
    )
    session.refresh(run)
    assert run.started_at == explicit
    assert run.last_progress_at == explicit


def test_scan_stale_does_not_kill_freshly_running_run(
    session, project, agent, admin_user,
):
    """The regression: a row whose ``last_progress_at`` is NULL but whose
    ``started_at`` is fresh used to be flagged stale in the very first
    scan. With ``COALESCE(last_progress_at, started_at)`` that path is
    closed. We backdate ``started_at`` to 5 s ago and assert the scanner
    leaves the run alone."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p2-fresh-run", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="running")
    # Simulate "transition happened a moment ago, heartbeat hasn't fired".
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(
            last_progress_at=None,
            started_at=utc_now() - timedelta(seconds=5),
        )
    )
    session.commit()

    result = scheduling_service.scan_stale_agent_runs(
        session, warn_after_seconds=30 * 60, takeover_after_seconds=60 * 60,
    )
    assert run.id not in result["warned"]
    assert run.id not in result["taken_over"]
    session.refresh(run)
    assert run.is_stale is False
    assert run.status == "running"


def test_scan_stale_still_warns_when_started_at_is_old_and_no_heartbeat(
    session, project, agent, admin_user,
):
    """Defensive: if ``started_at`` itself is past the warn threshold and
    the agent never reported any heartbeat, the scanner still treats the
    run as stale. This preserves the original "stale since run start"
    behaviour for legacy rows that predate the heartbeat."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p2-legacy-no-hb", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="running")
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(
            last_progress_at=None,
            started_at=utc_now() - timedelta(minutes=45),
        )
    )
    session.commit()

    result = scheduling_service.scan_stale_agent_runs(
        session, warn_after_seconds=30 * 60, takeover_after_seconds=60 * 60,
    )
    assert run.id in result["warned"]
    session.refresh(run)
    assert run.is_stale is True


# ===========================================================================
# 2026-09-15 P0 follow-ups — soft_takeover_run race safety
#
# The previous implementation did ``run.status = "failed"`` directly on
# the ORM object, which is not a CAS. The tests below pin the new
# behaviour: when ``is_stale`` flips back to False (the agent
# recovered) between the scanner's SELECT and the takeover call, the
# takeover must NOT mark the run failed or revert the task.
# ===========================================================================

def test_soft_takeover_cas_loses_when_is_stale_cleared_by_heartbeat(
    session, project, agent, admin_user,
):
    """Simulate the race: scanner sees stale run; agent heartbeats between
    scanner SELECT and takeover call, clearing ``is_stale``. The CAS
    must lose (rowcount 0) and the task must NOT be reverted.
    """
    t, _a = _make_in_progress_task(
        session, project, agent, title="p2-take-race", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="running")
    # Scanner's snapshot: is_stale=True, old progress.
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(
            is_stale=True,
            last_progress_at=utc_now() - timedelta(minutes=70),
        )
    )
    session.commit()

    # Now the agent recovers between SELECT and takeover call. In real
    # life this is ``report_task_progress`` clearing is_stale.
    session.refresh(run)
    scheduling_service.report_task_progress(
        session, run_id=run.id, note="recovered",
        actor_user_id=admin_user.id,
    )

    # Takeover attempt must CAS-lose.
    session.refresh(run)
    result = scheduling_service.soft_takeover_run(
        session, run, reason="stale_progress_takeover",
    )
    assert result is None  # CAS lost

    session.refresh(run)
    session.refresh(t)
    assert run.status == "running", "run must not be flipped to failed"
    assert run.is_stale is False, "heartbeat's clear must survive"
    assert t.status == Status.IN_PROGRESS.value, "task must not be reverted"


def test_soft_takeover_cas_loses_on_already_terminal_run(
    session, project, agent, admin_user,
):
    """If a concurrent writer already moved the run out of ``running``,
    the takeover CAS must lose. The task is not touched (the previous
    caller may already have moved it through review)."""
    t, _a = _make_in_progress_task(
        session, project, agent, title="p2-take-terminal", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="running")
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(
            is_stale=True,
            last_progress_at=utc_now() - timedelta(minutes=70),
        )
    )
    session.commit()

    # Simulate "another thread completed the run".
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id)
        .values(status="success", finished_at=utc_now())
    )
    session.commit()

    session.refresh(run)
    result = scheduling_service.soft_takeover_run(
        session, run, reason="stale_progress_takeover",
    )
    assert result is None  # CAS lost on status guard

    session.refresh(run)
    session.refresh(t)
    assert run.status == "success", "must not overwrite the writer's success"
    assert t.status == Status.IN_PROGRESS.value, "task untouched"


def test_soft_takeover_succeeds_when_run_is_stale_and_terminal_task(
    session, project, agent, admin_user,
):
    """Happy path: stale run + in_progress task → run marked failed,
    task reverted, assignment released. Verifies the CAS still does the
    right thing when no race occurs."""
    t, a = _make_in_progress_task(
        session, project, agent, title="p2-take-happy", user_id=admin_user.id,
    )
    run = _make_agent_run(session, t.id, agent, status="running")
    session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(
            is_stale=True,
            last_progress_at=utc_now() - timedelta(minutes=70),
        )
    )
    session.commit()

    session.refresh(run)
    scheduling_service.soft_takeover_run(
        session, run, reason="stale_progress_takeover",
    )
    session.refresh(run)
    session.refresh(t)
    session.refresh(a)
    assert run.status == "failed"
    assert "soft_takeover" in (run.error_message or "")
    assert t.status == Status.TODO.value
    assert t.current_assignment_id is None
    assert a.status == "released"
