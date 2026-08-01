"""compatibility marker for the legacy proposal migration

Revision ID: f7a6b5c4d3e2
Revises: e1f2a3b4c5d6

Older local installations used this revision to create the first Proposal
schema.  The migration was later replaced upstream, leaving those databases
at an unknown Alembic revision.  Keep the identifier in the graph as a no-op;
the h4 migration detects and upgrades the legacy tables when they exist.
"""

revision = "f7a6b5c4d3e2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
