#!/usr/bin/env python
"""索引创建辅助脚本

为现有数据库创建缺失的性能索引。
支持 SQLite 和 MariaDB 自动检测。

使用方法：
    # 使用默认数据库
    python scripts/create_indexes.py

    # 使用指定数据库
    AGENTBOARD_DB_URL=mysql+pymysql://user:pass@host:3306/db python scripts/create_indexes.py
"""
import os
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect, create_engine

# 导入 database 模块获取 engine 和 URL
from agentboard import database


def get_index_sql(dialect: str) -> list[tuple[str, str]]:
    """返回需要创建的索引及其名称

    Returns:
        list of (index_name, sql) tuples
    """
    if dialect == "mysql":
        return [
            ("ix_tasks_project_status", "CREATE INDEX ix_tasks_project_status ON tasks (project_id, status)"),
            ("ix_tasks_project_priority", "CREATE INDEX ix_tasks_project_priority ON tasks (project_id, priority)"),
            ("ix_tasks_status", "CREATE INDEX ix_tasks_status ON tasks (status)"),
            ("ix_epics_project_status", "CREATE INDEX ix_epics_project_status ON epics (project_id, status)"),
            ("ix_stories_epic_status", "CREATE INDEX ix_stories_epic_status ON stories (epic_id, status)"),
            ("ix_sprints_project_status", "CREATE INDEX ix_sprints_project_status ON sprints (project_id, status)"),
        ]
    else:  # sqlite
        return [
            ("ix_tasks_project_status", "CREATE INDEX IF NOT EXISTS ix_tasks_project_status ON tasks (project_id, status)"),
            ("ix_tasks_project_priority", "CREATE INDEX IF NOT EXISTS ix_tasks_project_priority ON tasks (project_id, priority)"),
            ("ix_tasks_status", "CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks (status)"),
            ("ix_epics_project_status", "CREATE INDEX IF NOT EXISTS ix_epics_project_status ON epics (project_id, status)"),
            ("ix_stories_epic_status", "CREATE INDEX IF NOT EXISTS ix_stories_epic_status ON stories (epic_id, status)"),
            ("ix_sprints_project_status", "CREATE INDEX IF NOT EXISTS ix_sprints_project_status ON sprints (project_id, status)"),
        ]


def get_existing_indexes(engine) -> set[str]:
    """获取数据库中已存在的索引名称"""
    inspector = inspect(engine)
    existing = set()
    for table in inspector.get_table_names():
        for idx in inspector.get_indexes(table):
            existing.add(idx["name"])
    return existing


def main():
    print("=" * 50)
    print("AgentBoard 索引创建工具")
    print("=" * 50)

    # 使用 database 模块的 engine
    engine = database.engine

    # 检测数据库类型
    dialect = engine.dialect.name
    print(f"\n数据库类型: {dialect}")

    # 获取已存在的索引
    existing = get_existing_indexes(engine)
    print(f"已存在索引数: {len(existing)}")

    # 获取需要创建的索引
    indexes = get_index_sql(dialect)

    # 筛选出尚未创建的索引
    to_create = [(name, sql) for name, sql in indexes if name not in existing]

    if not to_create:
        print("\n所有索引已存在，无需创建。")
        return 0

    print(f"\n需要创建的索引 ({len(to_create)}):")
    for name, _ in to_create:
        print(f"  - {name}")

    # 确认操作
    confirm = input("\n确认创建索引? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("已取消。")
        return 1

    # 创建索引
    print("\n开始创建索引...")
    with engine.connect() as conn:
        for name, sql in to_create:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✓ {name}")
            except Exception as e:
                print(f"  ✗ {name}: {e}")

    print("\n索引创建完成！")
    print(f"新创建索引数: {len(to_create)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
