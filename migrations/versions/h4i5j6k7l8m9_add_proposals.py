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
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("proposals"):
        _upgrade_legacy_schema(inspector)
        return

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


def _upgrade_legacy_schema(inspector: sa.Inspector) -> None:
    """Convert the pre-upstream Proposal schema without deleting user data."""
    proposal_columns = {column["name"] for column in inspector.get_columns("proposals")}
    if "body" in proposal_columns and "content" not in proposal_columns:
        with op.batch_alter_table("proposals") as batch:
            batch.alter_column(
                "body", new_column_name="content", existing_type=sa.Text(), nullable=True,
            )
    if "created_by" in proposal_columns and "author_id" not in proposal_columns:
        with op.batch_alter_table("proposals") as batch:
            batch.alter_column(
                "created_by", new_column_name="author_id",
                existing_type=sa.Integer(), nullable=True,
            )

    round_columns = {
        column["name"] for column in inspector.get_columns("proposal_rounds")
    }
    with op.batch_alter_table("proposal_rounds") as batch:
        if "round_number" in round_columns and "round_no" not in round_columns:
            batch.alter_column(
                "round_number", new_column_name="round_no",
                existing_type=sa.Integer(), nullable=False,
            )
        if "updated_at" not in round_columns:
            batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    question_columns = {
        column["name"] for column in inspector.get_columns("proposal_questions")
    }
    with op.batch_alter_table("proposal_questions") as batch:
        if "seq" not in question_columns:
            batch.add_column(sa.Column("seq", sa.Integer(), nullable=True))
        if "answered_by" not in question_columns:
            batch.add_column(sa.Column("answered_by", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_proposal_questions_answered_by_users",
                "users", ["answered_by"], ["id"], ondelete="SET NULL",
            )
        if "updated_at" not in question_columns:
            batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        if "round_number" in question_columns:
            batch.alter_column(
                "round_number", existing_type=sa.Integer(), nullable=True,
            )

    question_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("proposal_questions")
    }
    if "ix_proposal_questions_answered_by" not in question_indexes:
        op.create_index(
            "ix_proposal_questions_answered_by", "proposal_questions", ["answered_by"],
        )

def downgrade() -> None:
    op.drop_table("proposal_questions")
    op.drop_table("proposal_rounds")
    op.drop_table("proposals")
