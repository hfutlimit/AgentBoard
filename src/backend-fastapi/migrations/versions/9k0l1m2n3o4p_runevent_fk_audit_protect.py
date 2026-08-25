"""Protect RunEvent audit rows from losing their API-key attribution.

Revision ID: 9k0l1m2n3o4p
Revises: 4be8f9a0c1d2
Create Date: 2026-08-25

P1-8 review follow-up: the previous actor-identity migration
``3ad7e2f1c4b8`` set ``agent_run_events.api_key_id`` to ``ON DELETE
SET NULL``. That is too lossy for an audit table — if an API key is
rotated (the very case the audit trail exists to investigate), every
RunEvent that was emitted under that key loses its FK and we are left
relying only on the ``api_key_prefix_snapshot`` string.

This migration changes the FK to ``ON DELETE RESTRICT`` so any future
``DELETE FROM api_keys WHERE id = ?`` is blocked at the DB level while
RunEvent rows still reference it. The remaining identity FKs
(``actor_user_id``, ``agent_registry_id``) keep ``SET NULL`` because the
``*_snapshot`` columns are sufficient to identify the historical actor
in those cases; the audit row remains readable, just without a live
link to a row that may no longer exist.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9k0l1m2n3o4p"
down_revision: Union[str, None] = "4be8f9a0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_agent_run_events_api_key_id_api_keys",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_agent_run_events_api_key_id_api_keys",
            "api_keys",
            ["api_key_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_agent_run_events_api_key_id_api_keys",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_agent_run_events_api_key_id_api_keys",
            "api_keys",
            ["api_key_id"],
            ["id"],
            ondelete="SET NULL",
        )
