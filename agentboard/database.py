import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

DEFAULT_URL = "sqlite:///./agentboard.db"
URL = os.getenv("AGENTBOARD_DB_URL", DEFAULT_URL)

_connect_args = {"check_same_thread": False} if URL.startswith("sqlite") else {}
engine = create_engine(URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    # 简易版：直接建表。生产迁移后续用 Alembic 替代。
    import agentboard.models  # noqa: F401  (确保模型注册)
    agentboard.models.Base.metadata.create_all(engine)


def get_session() -> Session:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
