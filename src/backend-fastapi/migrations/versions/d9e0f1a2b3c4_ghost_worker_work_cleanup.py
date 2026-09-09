"""Delete ghost worker_work rows (Story 434 / 2026-09-09).

Revision ID: d9e0f1a2b3c4
Revises: b3c4d5e6f7g8
Create Date: 2026-09-09

``worker_work`` rows written before ``Offer`` enforced its entity reference carry
``entity_type=''`` / ``entity_id=0``. They can never resolve to a Proposal/Task,
yet the relay kept republishing them, so every consumer claim failed and real work
starved (2026-09-09 incident: Work 18/21/24/26/28/29/32/97).

The runtime now refuses those rows in ``resolve_claim`` (409 ``ghost_work_row``)
and skips them in ``drain_once``, and ``POST /api/admin/worker-work/cleanup-ghost``
can clear them per environment. This migration makes the cleanup part of the
schema upgrade itself, so every environment converges on ``alembic upgrade head``
instead of waiting for an operator to notice a stalled queue.

Idempotent by construction: the predicate matches nothing once the rows are gone,
and it is a no-op on databases where ``worker_work`` has not been created yet.
The predicate mirrors ``GHOST_WHERE_SQL`` in features/scheduling/worker_work.py,
which is the single source of truth; keep the two in sync if either changes.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text


revision = "d9e0f1a2b3c4"
down_revision = "b3c4d5e6f7g8"

_GHOST_WHERE = (
    "entity_id IS NULL OR entity_id <= 0 "
    "OR entity_type IS NULL OR entity_type = '' "
    "OR entity_type NOT IN ('proposal', 'task')"
)


def upgrade() -> None:
    bind = op.get_bind()
    if "worker_work" not in inspect(bind).get_table_names():
        return
    op.execute(text(f"DELETE FROM worker_work WHERE {_GHOST_WHERE}"))


def downgrade() -> None:
    # Data removal is not reversible, and re-creating unclaimable rows would be
    # actively harmful; the down migration intentionally does nothing.
    pass
