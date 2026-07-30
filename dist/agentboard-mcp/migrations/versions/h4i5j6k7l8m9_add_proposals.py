"""add proposals / proposal_rounds / proposal_questions (Epic 96 P0：Proposal 澄清回路)

Revision ID: h4i5j6k7l8m9
Revises: g3h4i5j6k7l8

新增三张表承载「人机协同需求分析」的澄清回路，不修改任何既有表结构。
``(proposal_id, round_no)`` 唯一约束用于 at-least-once 消息投递的幂等兜底。
双后端兼容（SQLite / MariaDB）。
"""
from alembic import op
import sqlalchemy as sa

revision = "h4i5j6k7l8m9"
down_revision = "g3h4i5j6k7l8"
branch_labels = None
depends_on = None

_STATUS_CHECK = (
    "status IN ('draft','queued','analyzing','awaiting','answered',"
    "'converged','story_created','failed')"
)


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("current_round", sa.Integer(), nullable=True),
        sa.Column("converged_spec", sa.Text(), nullable=True),
        sa.Column("story_id", sa.Integer(), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_proposals_status"),
        sa.Index("ix_proposals_project_id", "project_id"),
        sa.Index("ix_proposals_status", "status"),
        sa.Index("ix_proposals_story_id", "story_id"),
        sa.Index("ix_proposals_author_id", "author_id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "proposal_rounds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("agent", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "proposal_id", "round_no", name="uq_proposal_rounds_proposal_round",
        ),
        sa.Index("ix_proposal_rounds_proposal_id", "proposal_id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "proposal_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("unsure", sa.Boolean(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("answered_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["round_id"], ["proposal_rounds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["answered_by"], ["users.id"], ondelete="SET NULL"),
        sa.Index("ix_proposal_questions_proposal_id", "proposal_id"),
        sa.Index("ix_proposal_questions_round_id", "round_id"),
        sa.Index("ix_proposal_questions_answered_by", "answered_by"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("proposal_questions")
    op.drop_table("proposal_rounds")
    op.drop_table("proposals")
