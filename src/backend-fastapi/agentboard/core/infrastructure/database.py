"""Database engine, session factory, and UnitOfWork.

提供:
- ``engine`` / ``SessionLocal``: SQLAlchemy 入口
- ``session_scope()`` / ``get_session()``: 事务上下文
- ``UnitOfWork``: 面向 service 层的统一事务边界(Phase 4 启用,本阶段先放占位)
- ``init_db()``: 启动时跑 Alembic
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Protocol

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "sqlite:///./agentboard.db"
URL = os.getenv("AGENTBOARD_DB_URL", DEFAULT_URL)

_connect_args = {"check_same_thread": False} if URL.startswith("sqlite") else {}
engine: Engine = create_engine(URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


if URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # NORMAL: 仅在 checkpoint(而非每次提交)刷盘,彻底消除 Windows 上单连接
        # 多提交(端点提交 + 状态机提交 + 审计写入)累积的秒级 fsync 延迟;
        # 仅在 OS 断电时存在极小损坏风险,对开发/调试 SQLite 完全可接受。
        # 不影响生产 MariaDB(该分支仅在 sqlite URL 下生效)。
        cursor.execute("PRAGMA synchronous=NORMAL")
        # 多个 Worker 并发认领同一提案时,SQLite 的写锁会把它们串行化(这正是
        # CAS 认领得以原子的前提)。默认 5s busy timeout 在并发下偏紧,抬到 10s
        # 让后到者安静排队并读到已提交状态(从而 rowcount=0 判负),
        # 而不是抛 "database is locked"。
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


def init_db() -> None:
    """将数据库升级到最新版本;迁移失败时中止启动,避免带病运行。"""
    _run_alembic()


def reset_engine() -> None:
    """Re-bind ``engine`` and ``SessionLocal`` to a fresh engine.

    Reads ``AGENTBOARD_DB_URL`` (or the default) at call time and
    rebuilds the module-level ``engine`` and ``SessionLocal`` against
    it. Used by test fixtures that toggle the env var to point at a
    per-test temp file and need the next ``get_session()`` /
    ``SessionLocal()`` to follow. Production code never calls this
    — the engine is built once at module import time and is stable
    for the lifetime of the FastAPI process.

    The Alembic connection held by ``_run_alembic`` is not refreshed
    here; if a test that toggles the URL also wants migrations to
    run against the new engine, call ``init_db()`` after
    ``reset_engine()``.
    """
    global engine, SessionLocal
    url = os.getenv("AGENTBOARD_DB_URL", DEFAULT_URL)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    )


def _run_alembic() -> None:
    from alembic import command
    from alembic.config import Config
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    # 显式传入本模块的连接,避免测试/多实例场景下 Alembic 重新导入到另一套 engine。
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供独立事务上下文(scheduler 等非 FastAPI 环境使用)。"""
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


def get_session() -> Iterator[Session]:
    """FastAPI 依赖项形式:每个请求一个 session,请求结束自动 commit/rollback。"""
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


# ---------------------------------------------------------------------------
# UnitOfWork(Phase 4 service 拆分时启用,本阶段先放协议 + 默认实现占位)
# ---------------------------------------------------------------------------


class UnitOfWork(Protocol):
    """事务边界协议,service 层通过 ``with uow.transaction() as s`` 统一管理 session。

    Phase 4 重构 service 时,所有 service 方法会改成接受 ``uow: UnitOfWork``,
    在 ``transaction()`` context 内做 ORM 操作;出 context 自动 commit + 缓存失效。
    当前所有调用点继续走 ``get_session()``,行为不变。
    """

    def transaction(self) -> "_Transaction":
        ...


class _Transaction:
    """UnitOfWork 默认实现:包装 SessionLocal session + commit/rollback 生命周期。

    使用示例::
        uow = SqlAlchemyUnitOfWork()
        with uow.transaction() as s:
            project = s.get(Project, pid)
            project.name = "new"
            # exit context → 自动 commit
    """

    def __init__(self) -> None:
        self._session: Session | None = None

    def __enter__(self) -> Session:
        self._session = SessionLocal()
        self._session.info["auto_commit"] = False
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._session is not None
        try:
            if exc_type is None:
                self._session.commit()
            else:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None


class SqlAlchemyUnitOfWork:
    """SQLAlchemy 实现的 UnitOfWork。Phase 4 启用。"""

    def transaction(self) -> _Transaction:
        return _Transaction()
