from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy registry used by every domain."""


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
