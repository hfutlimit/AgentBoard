"""Add immutable identity snapshots to RunEvent audit rows.

Revision ID: 4be8f9a0c1d2
Revises: 3ad7e2f1c4b8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4be8f9a0c1d2"
down_revision: Union[str, None] = "3ad7e2f1c4b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("actor_username_snapshot", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("api_key_prefix_snapshot", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("agent_ref_snapshot", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.drop_column("agent_ref_snapshot")
        batch_op.drop_column("api_key_prefix_snapshot")
        batch_op.drop_column("actor_username_snapshot")
