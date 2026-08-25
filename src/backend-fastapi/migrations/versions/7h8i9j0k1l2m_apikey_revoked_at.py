"""Soft-revoke API keys instead of physically deleting them.

Revision ID: 7h8i9j0k1l2m
Revises: 9k0l1m2n3o4p
Create Date: 2026-08-25

P0 follow-up to migration 9k0l1m2n3o4p: that migration made
``agent_run_events.api_key_id`` ``ON DELETE RESTRICT`` to protect the
audit trail, but the previous revoke path was a hard ``DELETE FROM
api_keys`` that would now hit an ``IntegrityError`` whenever a key
had ever been used to produce a run event. The user-facing fix is
to soft-revoke: flip ``enabled = 0`` and stamp ``revoked_at``.

Authentication still rejects the key (``lookup_api_key_by_hash``
filters on ``enabled``) and the audit FK chain stays intact because
the row is never removed. A future admin "purge" tool can still be
added on top of this for a hard cleanup once all related run events
have aged out.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7h8i9j0k1l2m"
down_revision: Union[str, None] = "9k0l1m2n3o4p"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_api_keys_revoked_at",
        "api_keys",
        ["revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_revoked_at", table_name="api_keys")
    op.drop_column("api_keys", "revoked_at")
