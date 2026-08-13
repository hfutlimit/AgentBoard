"""add AgentRun execution-time agent and model snapshot

Revision ID: h7i8j9k0l1m2
Revises: g6h7i8j9k0l1
"""
from alembic import op
import sqlalchemy as sa


revision = "h7i8j9k0l1m2"
down_revision = "g6h7i8j9k0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("agent", sa.String(length=64), nullable=True))
    op.add_column("agent_runs", sa.Column("model", sa.String(length=100), nullable=True))
    op.create_index("ix_agent_runs_agent", "agent_runs", ["agent"], unique=False)

    # Preserve the best available context for existing rows.  New rows are
    # snapshotted by service.create_run and no longer depend on mutable config.
    op.execute("""
        UPDATE agent_runs
        SET agent = (
            SELECT agent_schedules.agent
            FROM agent_schedules
            WHERE agent_schedules.id = agent_runs.schedule_id
        )
        WHERE agent IS NULL
    """)
    op.execute("""
        UPDATE agent_runs
        SET model = (
            SELECT agents.model
            FROM agents
            WHERE agents.agent_id = agent_runs.agent
        )
        WHERE model IS NULL AND agent IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_agent_runs_agent", table_name="agent_runs")
    op.drop_column("agent_runs", "model")
    op.drop_column("agent_runs", "agent")
