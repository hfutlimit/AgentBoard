"""add user profile fields

Revision ID: d6a1f4e8c2b7
Revises: 9f8c2e7d1a4c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6a1f4e8c2b7"
down_revision: Union[str, None] = "9f8c2e7d1a4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("display_name", sa.String(100), nullable=False, server_default=""))
        batch.add_column(sa.Column("email", sa.String(254), nullable=True))
        batch.add_column(sa.Column("avatar_url", sa.String(500), nullable=True))
        batch.create_unique_constraint("uq_users_email", ["email"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_email", type_="unique")
        batch.drop_column("avatar_url")
        batch.drop_column("email")
        batch.drop_column("display_name")
