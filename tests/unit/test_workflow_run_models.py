"""WorkflowRun models + facade (Active Workflows Overview slice 1, 2026-09-11).

Verifies:
- The models load via both ``features.workflow_runs.models`` and the
  ``domains.workflow_runs.models`` facade (the codebase imports from
  ``agentboard.models`` which re-exports both).
- The CHECK constraints on ``workflow_runs.status`` and ``phase`` are present
  (terminology: ``failed`` is strictly terminal; phase transitions are explicit).
- The ``workflow_type`` column has NO CHECK constraint (schema extensibility).
- All expected indexes exist for the project + status + reopen query patterns.
- Migration ``m5n6o7p8q9r0`` is the registered successor of
  ``z8a9b0c1d2e3`` in the alembic chain.
"""
import os

os.environ.setdefault("AGENTBOARD_DB_URL", "sqlite:///./_test_workflow_runs_models_tmp.db")

import pytest


def test_workflow_run_class_loads_via_features():
    from agentboard.features.workflow_runs.models import (
        WORKFLOW_RUN_STATUSES,
        WORKFLOW_RUN_PHASES,
        WORKFLOW_RUN_TERMINAL_STATUSES,
        WorkflowRun,
        WorkflowRunEvent,
    )
    assert WorkflowRun.__tablename__ == "workflow_runs"
    assert WorkflowRunEvent.__tablename__ == "workflow_run_events"
    assert "queued" in WORKFLOW_RUN_STATUSES
    assert "failed" in WORKFLOW_RUN_STATUSES
    assert WORKFLOW_RUN_TERMINAL_STATUSES == frozenset({"completed", "failed", "cancelled"})
    assert set(WORKFLOW_RUN_PHASES) == {"design", "development", "qa"}


def test_workflow_run_class_loads_via_domains_facade():
    """The codebase imports from agentboard.models; ensure the facade works."""
    from agentboard.domains.workflow_runs.models import (
        WorkflowRun,
        WorkflowRunEvent,
    )
    assert WorkflowRun.__name__ == "WorkflowRun"
    assert WorkflowRunEvent.__name__ == "WorkflowRunEvent"


def test_workflow_run_class_loads_via_root_models_facade():
    """agentboard.models re-exports WorkflowRun/Event for service layer use."""
    from agentboard.models import (
        WORKFLOW_RUN_PHASES,
        WORKFLOW_RUN_STATUSES,
        WORKFLOW_RUN_TERMINAL_STATUSES,
        WorkflowRun,
        WorkflowRunEvent,
    )
    assert WorkflowRun is not None
    assert WorkflowRunEvent is not None
    assert "failed" in WORKFLOW_RUN_STATUSES


def test_workflow_run_status_check_constraint_present():
    """The status CHECK constraint must list every legal status, including
    ``failed`` (terminal, but still a legal value to insert once)."""
    from agentboard.features.workflow_runs.models import WorkflowRun

    constraint_names = {c.name for c in WorkflowRun.__table__.constraints if hasattr(c, "name")}
    assert "ck_workflow_runs_status" in constraint_names


def test_workflow_run_phase_check_constraint_present():
    from agentboard.features.workflow_runs.models import WorkflowRun
    constraint_names = {c.name for c in WorkflowRun.__table__.constraints if hasattr(c, "name")}
    assert "ck_workflow_runs_phase" in constraint_names


def test_workflow_type_has_no_check_constraint():
    """v1: workflow_type is a free VARCHAR. No CHECK on it. Future types
    (schedule/proposal/ticket/deployment) can land without a schema migration."""
    from agentboard.features.workflow_runs.models import WorkflowRun

    check_names = {
        c.name for c in WorkflowRun.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint" and getattr(c, "name", None)
    }
    workflow_type_checks = {n for n in check_names if "type" in (n or "")}
    assert not workflow_type_checks, (
        f"workflow_type should NOT have a CHECK constraint (schema extensibility), "
        f"but found: {workflow_type_checks}"
    )


def test_required_indexes_exist():
    from agentboard.features.workflow_runs.models import WorkflowRun, WorkflowRunEvent

    wr_indexes = {idx.name for idx in WorkflowRun.__table__.indexes}
    assert "ix_workflow_runs_project_status" in wr_indexes
    assert "ix_workflow_runs_story" in wr_indexes
    assert "ix_workflow_runs_schedule" in wr_indexes
    assert "ix_workflow_runs_reopened_from" in wr_indexes

    ev_indexes = {idx.name for idx in WorkflowRunEvent.__table__.indexes}
    assert "ix_workflow_events_run_id_desc" in ev_indexes
    assert "ix_workflow_events_task" in ev_indexes
    assert "ix_workflow_events_agent_run" in ev_indexes


def test_workflow_run_event_actor_check_constraint_present():
    from agentboard.features.workflow_runs.models import WorkflowRunEvent
    constraint_names = {c.name for c in WorkflowRunEvent.__table__.constraints if hasattr(c, "name")}
    assert "ck_workflow_events_actor" in constraint_names


def test_reopened_from_run_id_is_self_reference():
    """workflow_runs.reopened_from_run_id must reference workflow_runs.id
    (a Story reopen creates a new run that points to the previous terminal one)."""
    from agentboard.features.workflow_runs.models import WorkflowRun

    col = WorkflowRun.__table__.columns["reopened_from_run_id"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "workflow_runs"
    assert fks[0].column.name == "id"
    # ON DELETE SET NULL: when an old run is hard-deleted, the new run's
    # pointer is nulled rather than cascade-deleting the new history.
    assert fks[0].ondelete == "SET NULL"


# ---------- Migration registration ----------

def test_migration_file_present():
    import importlib.util
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
    migration_path = (
        backend
        / "migrations"
        / "versions"
        / "m5n6o7p8q9r0_create_workflow_runs_and_events.py"
    )
    assert migration_path.exists(), f"migration file missing: {migration_path}"

    spec = importlib.util.spec_from_file_location("_test_migration", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "m5n6o7p8q9r0"
    assert module.down_revision == "z8a9b0c1d2e3"
    assert callable(module.upgrade)
    assert callable(module.downgrade)
