"""Alembic 环境：复用项目已配置的 engine 与 Base.metadata。"""
import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

from agentboard.database import engine, URL
from agentboard.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", URL)
if config.config_file_name and os.path.exists(config.config_file_name):
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # 兼容 SQLite/MariaDB 的 ALTER（batch 模式）
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")

    def run(connection) -> None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    if supplied_connection is not None:
        run(supplied_connection)
    else:
        with engine.connect() as connection:
            run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
