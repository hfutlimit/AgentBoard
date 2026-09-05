"""Worker-owned durable work offers and fenced results.

Revision ID: f72b8c06d941
Revises: ecd53de91def
"""
from alembic import op
import sqlalchemy as sa

revision = "f72b8c06d941"
down_revision = "ecd53de91def"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("worker_work",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_key", sa.String(160), nullable=False, unique=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("active_slot", sa.String(10), nullable=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("attempt_history", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", "active_slot", name="uq_worker_work_active_item"))
    for name in ("project_id", "entity_id", "state"):
        op.create_index(f"ix_worker_work_{name}", "worker_work", [name])


def downgrade():
    op.drop_table("worker_work")
