"""merge workflow outbox head into the latest m6 (T6.3) head

Revision ID: ecd53de91def
Revises: u9v0w1x2y3z4, wo_2026_08_28
Create Date: 2026-09-01 11:21:10.836126

The original proposal drafted this merge against ``u2v3w4x5y6z7``,
but main has since advanced through WorkerProjectMapping
(``u9v0w1x2y3z4``) so the actual head is now ``u9v0w1x2y3z4``.
This merge has no schema changes; it only joins the workflow-outbox
line into the main line so ``alembic upgrade head`` has a single
target.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ecd53de91def'
down_revision: Union[str, None] = ('u9v0w1x2y3z4', 'wo_2026_08_28')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
