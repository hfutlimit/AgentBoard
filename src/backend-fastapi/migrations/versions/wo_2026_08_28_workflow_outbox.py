"""workflow outbox: durable record of every workflow event the system
wants to publish, written in the same DB transaction as the state
change that produced it.

Revision ID: wo_2026_08_28
Revises: z8a9b0c1d2e3
Create Date: 2026-08-28

GPT review 2026-08-26 (commit ``6e5ce0c``) called out that
``POST /api/tasks/{tid}/review`` could lose workflow events when
the MQ was down: the DB would commit, ``publish_workflow_event``
would silently return ``False``, and the implementation successor
would never be woken up. The P0 fix isolates the DB write behind a
single transaction with the outbox row, and a background
``OutboxPublisher`` drains the outbox to RabbitMQ.

This migration creates the table; the application logic lives in
``core/infrastructure/outbox.py``. The original commit
(``6e5ce0c``) explicitly mentioned "Outbox for DB+MQ atomicity
not yet implemented"; this migration closes that gap.
"""
from alembic import op
import sqlalchemy as sa


revision = "wo_2026_08_28"
down_revision = "c3d4e5f6g7h8"


def upgrade() -> None:
    # Use ``Integer`` (not BigInteger) for SQLite so SQLite's
    # ``AUTOINCREMENT`` works; the project's other tables use the same
    # pattern (see the variant on the SQLAlchemy side at
    # ``outbox.WorkflowOutbox.id``). MariaDB stores ``Integer`` as
    # 32-bit INT which is fine — the table is a publisher backlog, not
    # a primary fact table.
    op.create_table(
        "workflow_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=True),
        sa.Column("agent_id", sa.String(128), nullable=True),
        # Forward-compatible payload. Current callers (router.py::review_task)
        # do not write a payload, but the column is cheap and lets future
        # events carry extra fields without a schema change.
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # ``published_at IS NULL`` is the live backlog; non-NULL is the
        # drain history. The publisher scans oldest-first.
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    # Single-column indexes for ad-hoc lookups (debug, /health).
    op.create_index("ix_workflow_outbox_event", "workflow_outbox", ["event"])
    op.create_index("ix_workflow_outbox_entity_type", "workflow_outbox", ["entity_type"])
    op.create_index("ix_workflow_outbox_entity_id", "workflow_outbox", ["entity_id"])
    op.create_index("ix_workflow_outbox_published_at", "workflow_outbox", ["published_at"])
    # Hot path composite: "give me the next batch of unpublished rows,
    # oldest first". Without this the publisher scan is a full seq
    # scan + sort, which becomes painful after a broker outage.
    op.create_index(
        "ix_workflow_outbox_unpublished",
        "workflow_outbox",
        ["published_at", "created_at"],
    )


def downgrade() -> None:
    # Reverse is intentionally explicit (no-op) — the table is small
    # and there is no production rollback path for the design change
    # here. Operator should drop the index/table manually if needed.
    raise NotImplementedError(
        "workflow_outbox is required for review_task to publish events; "
        "downgrade not supported"
    )
