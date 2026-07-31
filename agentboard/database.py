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
        # NORMAL：仅在检查点（而非每次提交）刷盘，彻底消除 Windows 上单连接
        # 多提交（端点提交 + 状态机提交 + 审计写入）累积的秒级 fsync 延迟；
        # 仅在 OS 断电时存在极小损坏风险，对开发/调试 SQLite 完全可接受。
        # 不影响生产 MariaDB（该分支仅在 sqlite URL 下生效）。
        cursor.execute("PRAGMA synchronous=NORMAL")
        # 多个 Worker 并发认领同一提案时，SQLite 的写锁会把它们串行化（这正是
        # CAS 认领得以原子的前提）。默认 5s busy timeout 在并发下偏紧，抬到 10s
        # 让后到者安静排队并读到已提交状态（从而 rowcount=0 判负），
        # 而不是抛 "database is locked"。
        cursor.execute("PRAGMA busy_timeout=10000")
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
