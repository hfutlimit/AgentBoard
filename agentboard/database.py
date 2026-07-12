import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

DEFAULT_URL = "sqlite:///./agentboard.db"
URL = os.getenv("AGENTBOARD_DB_URL", DEFAULT_URL)

_connect_args = {"check_same_thread": False} if URL.startswith("sqlite") else {}
engine = create_engine(URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


if URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    """将数据库升级到最新版本；迁移失败时中止启动，避免带病运行。"""
    _run_alembic()


def _run_alembic() -> None:
    from alembic.config import Config
    from alembic import command
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    # 显式传入本模块的连接，避免测试/多实例场景下 Alembic 重新导入到另一套 engine。
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


@contextmanager
def session_scope():
    """提供独立事务上下文（scheduler 等非 FastAPI 环境使用）。"""
    s = SessionLocal()
    s.info["auto_commit"] = False
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Session:
    s = SessionLocal()
    s.info["auto_commit"] = False
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
