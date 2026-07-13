"""Add performance indexes for common query patterns

Epic 18: API 性能优化 - 数据库查询优化
添加复合索引优化常见查询模式
"""
from alembic import op

revision = '9f8c2e7d1a4b'
down_revision = 'c4e8a1b2d3f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tasks: 复合索引 - project_id + status (常见按项目和状态过滤)
    op.create_index(
        'ix_tasks_project_status',
        'tasks',
        ['project_id', 'status'],
        unique=False,
        if_not_exists=True,
    )

    # Tasks: 复合索引 - project_id + priority (按优先级排序)
    op.create_index(
        'ix_tasks_project_priority',
        'tasks',
        ['project_id', 'priority'],
        unique=False,
        if_not_exists=True,
    )

    # Tasks: 单字段 status 索引 (任务列表常用)
    op.create_index(
        'ix_tasks_status',
        'tasks',
        ['status'],
        unique=False,
        if_not_exists=True,
    )

    # Epics: 复合索引 - project_id + status
    op.create_index(
        'ix_epics_project_status',
        'epics',
        ['project_id', 'status'],
        unique=False,
        if_not_exists=True,
    )

    # Stories: 复合索引 - epic_id + status
    op.create_index(
        'ix_stories_epic_status',
        'stories',
        ['epic_id', 'status'],
        unique=False,
        if_not_exists=True,
    )

    # Sprints: 复合索引 - project_id + status (查找活跃 Sprint)
    op.create_index(
        'ix_sprints_project_status',
        'sprints',
        ['project_id', 'status'],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index('ix_sprints_project_status', table_name='sprints', if_exists=True)
    op.drop_index('ix_stories_epic_status', table_name='stories', if_exists=True)
    op.drop_index('ix_epics_project_status', table_name='epics', if_exists=True)
    op.drop_index('ix_tasks_status', table_name='tasks', if_exists=True)
    op.drop_index('ix_tasks_project_priority', table_name='tasks', if_exists=True)
    op.drop_index('ix_tasks_project_status', table_name='tasks', if_exists=True)
