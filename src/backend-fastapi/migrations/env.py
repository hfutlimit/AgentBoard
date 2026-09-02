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
        # disable_existing_loggers=False 必须显式给：fileConfig 的默认值是
        # True，会把「fileConfig 执行前就已创建」的所有 logger 全部置
        # disabled=True —— 而 init_db() 在服务启动路径上跑，先于它创建的
        # 业务 logger（features/*/service 的模块级 log）从此一条日志都
        # 不再输出，且无任何报错。这是静默吞日志的坑，实测确认过：
        #   before init_db disabled=False → after init_db disabled=True
        # alembic 官方文档同样建议按需关闭该选项。
        fileConfig(config.config_file_name, disable_existing_loggers=False)
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
            # SQLite 下 batch_alter_table 会走 "create_tmp -> copy -> DROP -> rename"，
            # 但其它表对 projects 的 FK 会让 DROP 阶段被外键约束拒绝。
            # 迁移期间临时关闭 FK 校验（迁移完事务结束自动恢复），并加 try/rollback 兜底。
            is_sqlite = URL.startswith("sqlite")
            if is_sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
                try:
                    run(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            else:
                run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
