"""add attachments table

Revision ID: 9e4f1d5c8a3b
Revises: 7d3e9f0a1c2b
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e4f1d5c8a3b'
down_revision: Union[str, None] = '7d3e9f0a1c2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('original_name', sa.String(500), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attachments_task_id', 'attachments', ['task_id'])


def downgrade() -> None:
    op.drop_table('attachments')
