import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

DEFAULT_URL = "sqlite:///./agentboard.db"
URL = os.getenv("AGENTBOARD_DB_URL", DEFAULT_URL)

_connect_args = {"check_same_thread": False} if URL.startswith("sqlite") else {}
engine = create_engine(URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    # 简易版：直接建表 + 轻量在线迁移（避免每次引 Alembic）。
    import agentboard.models  # noqa: F401  (确保模型注册)
    agentboard.models.Base.metadata.create_all(engine)
    _ensure_migrations()


def _ensure_migrations() -> None:
    from sqlalchemy import inspect, text
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
        if "source_spec_id" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN source_spec_id INTEGER"))
            conn.commit()


def get_session() -> Session:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
