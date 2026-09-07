"""Merge the two open heads into one chain.

Revision ID: c4d5e6f7g8h9
Revises: z8a9b0c1d2e3, f8e9d0c1b2a3
Create Date: 2026-09-07

Both ``z8a9b0c1d2e3`` (task status simplify, Story 265) and
``f8e9d0c1b2a3`` (Epic blocked status) were already heads when the
2026-09-07 ``b3c4d5e6f7g8`` (task_assignments 'superseded') migration
landed, so a single linear upgrade to ``head`` could not pick one
without diverging. This empty merge collapses both lineages so
``b3c4d5e6f7g8`` can extend a single head and ``alembic upgrade head``
stops failing in fresh SQLite test setups.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7g8h9"
down_revision = ("z8a9b0c1d2e3", "f8e9d0c1b2a3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
