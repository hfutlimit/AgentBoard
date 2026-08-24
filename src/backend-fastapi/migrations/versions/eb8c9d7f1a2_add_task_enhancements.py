"""Add task enhancements: assignee_id, due_date, labels

Epic 17: 任务管理增强 - 标签、负责人、截止日期
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'eb8c9d7f1a2'
down_revision = '1a2b3c4d5e6f'  # 最新迁移版本（在 add_project_members_notifications 之后）
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 检查列是否已存在，如果不存在则添加
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c['name'] for c in inspector.get_columns('tasks')]

    # 添加 assignee_id (不使用外键，SQLite 不支持 ALTER 添加约束)
    if 'assignee_id' not in existing_columns:
        op.add_column('tasks', sa.Column('assignee_id', sa.Integer(), nullable=True))

    # 添加 due_date
    if 'due_date' not in existing_columns:
        op.add_column('tasks', sa.Column('due_date', sa.Date(), nullable=True, index=True))

    # 添加 labels (JSON 字符串)
    if 'labels' not in existing_columns:
        op.add_column('tasks', sa.Column('labels', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    # 只在列存在时删除
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = [c['name'] for c in inspector.get_columns('tasks')]

    if 'labels' in existing_columns:
        op.drop_column('tasks', 'labels')
    if 'due_date' in existing_columns:
        op.drop_column('tasks', 'due_date')
    if 'assignee_id' in existing_columns:
        op.drop_column('tasks', 'assignee_id')
