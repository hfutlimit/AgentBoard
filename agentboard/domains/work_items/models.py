from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..common.enums import ItemType, Priority, Status
from ..common.models import Base, utc_now


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("type IN ('task','bug')", name="ck_tasks_type"),
        CheckConstraint("status IN ('backlog','todo','in_progress','in_review','verifying','done')", name="ck_tasks_status"),
        CheckConstraint("priority IN ('highest','high','medium','low','lowest')", name="ck_tasks_priority"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"), nullable=True, index=True)
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(10), default=ItemType.TASK)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=Status.BACKLOG)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM)
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[str] = mapped_column(Text, default="")
    source_spec_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
