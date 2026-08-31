"""Focused upgrade/downgrade proof for the Golden Happy Path migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "src/backend-fastapi/migrations/versions"
    / "u2v3w4x5y6z7_golden_happy_path_contracts.py"
)
BACKFILL_PATH = REPO_ROOT / "scripts/backfill-agent-executor-type.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("golden_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_schema(engine) -> None:
    metadata = sa.MetaData()
    sa.Table("epics", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "proposals",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    sa.Table(
        "agent_instances",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cli_command", sa.String(500), nullable=False, server_default=""),
    )
    sa.Table(
        "tasks",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    sa.Table(
        "proposal_ticket_requests",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "type IN ('auto','bug','epic','story','task')",
            name="ck_ticket_req_type",
        ),
    )
    metadata.create_all(engine)


def test_golden_migration_upgrade_and_downgrade(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'golden-migration.db'}")
    _old_schema(engine)
    migration = _load_migration()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    inspector = sa.inspect(engine)
    assert "target_epic_id" in {
        column["name"] for column in inspector.get_columns("proposals")
    }
    assert "executor_type" in {
        column["name"] for column in inspector.get_columns("agent_instances")
    }
    assert {"assignment_deferred_reason", "assignment_deferred_at"}.issubset({
        column["name"] for column in inspector.get_columns("tasks")
    })
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO proposal_ticket_requests (id, type) VALUES (1, 'auto_story')"
        ))

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.downgrade()

    inspector = sa.inspect(engine)
    assert "target_epic_id" not in {
        column["name"] for column in inspector.get_columns("proposals")
    }
    assert "executor_type" not in {
        column["name"] for column in inspector.get_columns("agent_instances")
    }
    assert "assignment_deferred_reason" not in {
        column["name"] for column in inspector.get_columns("tasks")
    }


def test_executor_backfill_is_dry_run_by_default_and_apply_is_explicit(tmp_path):
    database_path = tmp_path / "executor-backfill.db"
    database_url = f"sqlite:///{database_path}"
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE agents (
                agent_id VARCHAR(100) PRIMARY KEY,
                roles TEXT NOT NULL
            )
        """))
        connection.execute(sa.text("""
            CREATE TABLE agent_instances (
                id INTEGER PRIMARY KEY,
                agent_id VARCHAR(100) NOT NULL,
                cli_command VARCHAR(500) NOT NULL,
                executor_type VARCHAR(40)
            )
        """))
        connection.execute(sa.text("""
            INSERT INTO agents (agent_id, roles) VALUES
                ('a-cli', '[]'),
                ('a-role', '["workbuddy"]'),
                ('a-unknown', '[]')
        """))
        connection.execute(sa.text("""
            INSERT INTO agent_instances (id, agent_id, cli_command, executor_type) VALUES
                (1, 'a-cli', 'codex exec', NULL),
                (2, 'a-role', '', NULL),
                (3, 'a-unknown', '', NULL)
        """))

    dry_run = subprocess.run(
        [sys.executable, str(BACKFILL_PATH), "--database-url", database_url],
        check=False,
        capture_output=True,
        text=True,
    )
    assert dry_run.returncode == 2  # unresolved rows are surfaced to operators
    assert "mode=dry-run" in dry_run.stdout
    with engine.connect() as connection:
        assert connection.execute(sa.text(
            "SELECT count(*) FROM agent_instances WHERE executor_type IS NOT NULL"
        )).scalar_one() == 0

    applied = subprocess.run(
        [
            sys.executable,
            str(BACKFILL_PATH),
            "--database-url",
            database_url,
            "--apply",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 2
    assert "mode=apply" in applied.stdout
    with engine.connect() as connection:
        rows = connection.execute(sa.text(
            "SELECT id, executor_type FROM agent_instances ORDER BY id"
        )).all()
    assert rows == [(1, "codex"), (2, "workbuddy"), (3, None)]
