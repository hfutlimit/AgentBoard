"""Add task enhancements: assignee_id, due_date, labels

Epic 17: 任务管理增强 - 标签、负责人、截止日期
"""
from alembic import op
import sqlalchemy as sa

revision = 'eb8c9d7f1a2'
down_revision = 'a5f2e8d9b0c1'  # 最新的迁移版本
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加 assignee_id (外键到 users 表)
    op.add_column('tasks', sa.Column('assignee_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True, index=True))
    # 添加 due_date
    op.add_column('tasks', sa.Column('due_date', sa.Date(), nullable=True, index=True))
    # 添加 labels (JSON 字符串)
    op.add_column('tasks', sa.Column('labels', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('tasks', 'labels')
    op.drop_column('tasks', 'due_date')
    op.drop_column('tasks', 'assignee_id')
