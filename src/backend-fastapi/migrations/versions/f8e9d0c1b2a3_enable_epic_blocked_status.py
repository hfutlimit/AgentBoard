"""Allow blocked as a persisted Epic status.

Revision ID: f8e9d0c1b2a3
Revises: a19d58e204bc

The project Epic list exposes ``blocked`` as a business status.  The API
already accepts it through the shared status validator, but the Epic database
constraint previously rejected it at flush time.  Rebuild the constraint on
SQLite and alter it directly on MariaDB so PATCH /api/epics/{id} is consistent
with the published UI contract.
"""
from alembic import op


revision = "f8e9d0c1b2a3"
down_revision = "a19d58e204bc"
branch_labels = None
depends_on = None


_OLD_STATUS_CHECK = (
    "status IN ('backlog','todo','in_progress','in_review','verifying','done')"
)
_NEW_STATUS_CHECK = (
    "status IN ('backlog','todo','in_progress','in_review','verifying','done','blocked')"
)


def _replace_status_constraint(status_check: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("epics") as batch:
            batch.drop_constraint("ck_epics_status", type_="check")
            batch.create_check_constraint("ck_epics_status", status_check)
    else:
        op.drop_constraint("ck_epics_status", "epics", type_="check")
        op.create_check_constraint("ck_epics_status", "epics", status_check)


def upgrade() -> None:
    _replace_status_constraint(_NEW_STATUS_CHECK)


def downgrade() -> None:
    _replace_status_constraint(_OLD_STATUS_CHECK)
