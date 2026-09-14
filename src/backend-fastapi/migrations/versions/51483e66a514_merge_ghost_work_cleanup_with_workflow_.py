"""merge ghost_work cleanup with workflow_runs

Revision ID: 51483e66a514
Revises: d9e0f1a2b3c4, m5n6o7p8q9r0
Create Date: 2026-09-14 10:54:51.189571
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51483e66a514'
down_revision: Union[str, None] = ('d9e0f1a2b3c4', 'm5n6o7p8q9r0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
