"""Merge the workflow_runs and ghost cleanup heads (2026-09-11).

Revision ID: n2o3p4q5r6s7
Revises: m5n6o7p8q9r0, d9e0f1a2b3c4
Create Date: 2026-09-11

After slice 1 of the Active Workflows Overview landed (workflow_runs +
workflow_run_events tables, ``m5n6o7p8q9r0``) while the previously
deployed chain was still at ``d9e0f1a2b3c4`` (ghost worker_work
cleanup, 2026-09-09), ``alembic upgrade head`` started failing with
"Multiple head revisions are present". This empty merge collapses both
lineages so a single linear ``upgrade head`` is well-defined again and
the next migration can extend one head.

Mirror of the 2026-09-07 ``c4d5e6f7g8h9_merge_heads`` pattern (which
merged the task-status-simplify and epic-blocked-status heads).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "n2o3p4q5r6s7"
down_revision = ("m5n6o7p8q9r0", "d9e0f1a2b3c4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
