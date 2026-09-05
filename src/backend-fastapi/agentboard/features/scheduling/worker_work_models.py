"""Durable transport/lease records; no scheduling or provider configuration."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ...core.common.models import Base, utc_now


class WorkerWork(Base):
    __tablename__ = "worker_work"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "active_slot", name="uq_worker_work_active_item"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    iteration: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="available", index=True)
    active_slot: Mapped[str | None] = mapped_column(String(10), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_history: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
