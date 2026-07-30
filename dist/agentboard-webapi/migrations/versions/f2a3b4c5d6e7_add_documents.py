"""add documents & document_comments (Epic 15：项目文档维护)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("epic_id", sa.Integer(), nullable=True),
        sa.Column("story_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["epic_id"], ["epics.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "type IN ('memory','plan','knowledge','design')", name="ck_documents_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft','in_review','approved','cancelled')",
            name="ck_documents_status",
        ),
        sa.Index("ix_documents_project_id", "project_id"),
        sa.Index("ix_documents_epic_id", "epic_id"),
        sa.Index("ix_documents_story_id", "story_id"),
        sa.Index("ix_documents_author_id", "author_id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "document_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.Index("ix_document_comments_document_id", "document_id"),
        sa.Index("ix_document_comments_author_id", "author_id"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("document_comments")
    op.drop_table("documents")
