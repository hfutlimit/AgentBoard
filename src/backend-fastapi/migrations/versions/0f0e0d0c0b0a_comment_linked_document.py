"""comments.linked_document_id: cross-reference comment to a document

Revision ID: 0f0e0d0c0b0a
Revises: 0a0b0c0d0e0f
Create Date: 2026-09-14

Problem 2 of 2026-09-14: Document-Comment deduplication.

Background
----------
Today, an agent that produces a structured artifact (design doc, runbook,
post-mortem, test plan) has only two places to drop it:

1. Create a ``Document`` row (linked to project/epic/story).
2. Paste the same content into a ``Comment`` on the relevant task.

Both end up in the UI; the user sees the same text in two places and
the agent has done the work twice. Worse, the two copies drift on edits.

This migration adds a single nullable FK column on ``comments`` that lets
the agent (or any user) reference a document from a comment without
duplicating its body. The column is intentionally nullable: legacy
free-text comments are not affected, and the rule is opt-in per
comment-write.

- ``comments.linked_document_id`` (Integer, nullable) — points to
  ``documents.id``. ON DELETE SET NULL preserves the comment when the
  document is deleted (the body still tells the story; the link just
  goes cold).
- Index on the column for the "show me all comments referencing this
  document" reverse lookup the doc detail page needs.

The UI side (task drawer, doc detail) will read this column and render
the comment body as a collapsed link card when it's set; the body is
still stored in full for searchability, but the user gets a clean UX.

Down-migration drops the column and the index. No data loss beyond the
link itself (legacy comments keep their body).

Revision ID note: chained on top of ``0a0b0c0d0e0f`` (problem 1) so
both land atomically; the project keeps the convention of one
revision-id block per (Epic, Story) and these two are siblings under
the same umbrella.
"""
from alembic import op
import sqlalchemy as sa


revision = "0f0e0d0c0b0a"
down_revision = "0a0b0c0d0e0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("comments") as batch:
        batch.add_column(
            sa.Column("linked_document_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_comments_linked_document",
            "documents",
            ["linked_document_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_comments_linked_document_id",
            ["linked_document_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("comments") as batch:
        batch.drop_index("ix_comments_linked_document_id")
        batch.drop_constraint(
            "fk_comments_linked_document", type_="foreignkey"
        )
        batch.drop_column("linked_document_id")
