from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..common.enums import RunStatus, ScheduleType
from ..common.models import Base, utc_now


class AgentSchedule(Base):
    __tablename__ = "agent_schedules"
    __table_args__ = (CheckConstraint(
        "schedule_type IN ('once','cron')", name="ck_schedules_type",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(10), default=ScheduleType.CRON)
    cron_expr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (CheckConstraint(
        "status IN ('pending','running','success','failed')", name="ck_runs_status",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("agent_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.PENDING)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
