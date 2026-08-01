"""add proposal clarification workflow

Revision ID: f7a6b5c4d3e2
Revises: e1f2a3b4c5d6
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a6b5c4d3e2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("story_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("converged_spec", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','queued','analyzing','awaiting','answered','converged','story_created','failed')",
            name="ck_proposals_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposals_project_id", "proposals", ["project_id"])
    op.create_index("ix_proposals_created_by", "proposals", ["created_by"])
    op.create_index("ix_proposals_story_id", "proposals", ["story_id"])
    op.create_index("ix_proposals_status", "proposals", ["status"])

    op.create_table(
        "proposal_rounds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", "round_number", name="uq_proposal_round_number"),
    )
    op.create_index("ix_proposal_rounds_proposal_id", "proposal_rounds", ["proposal_id"])

    op.create_table(
        "proposal_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("unsure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('open','answered')", name="ck_proposal_questions_status"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["round_id"], ["proposal_rounds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposal_questions_proposal_id", "proposal_questions", ["proposal_id"])
    op.create_index("ix_proposal_questions_round_id", "proposal_questions", ["round_id"])


def downgrade() -> None:
    op.drop_table("proposal_questions")
    op.drop_table("proposal_rounds")
    op.drop_table("proposals")
