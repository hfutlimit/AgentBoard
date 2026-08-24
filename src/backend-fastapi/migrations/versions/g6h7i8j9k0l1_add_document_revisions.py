"""add document_revisions (Epic 139：不可变 Revision + 乐观锁)

Revision ID: g6h7i8j9k0l1
Revises: z8a9b0c1d2e3
"""
from alembic import op
import sqlalchemy as sa


revision = "g6h7i8j9k0l1"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("change_note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("is_restore", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("restored_from_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("document_id", "revision_number", name="uq_document_revisions_doc_revnum"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_revisions_document_id", "document_revisions", ["document_id"])
    op.create_index("ix_document_revisions_revision_number", "document_revisions", ["revision_number"])
    op.create_index("ix_document_revisions_author_id", "document_revisions", ["author_id"])
    op.create_index("ix_document_revisions_created_at", "document_revisions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_document_revisions_created_at", table_name="document_revisions")
    op.drop_index("ix_document_revisions_author_id", table_name="document_revisions")
    op.drop_index("ix_document_revisions_revision_number", table_name="document_revisions")
    op.drop_index("ix_document_revisions_document_id", table_name="document_revisions")
    op.drop_table("document_revisions")
