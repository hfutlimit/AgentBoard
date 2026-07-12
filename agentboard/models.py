from datetime import datetime, UTC
from enum import StrEnum
from sqlalchemy import CheckConstraint, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ItemType(StrEnum):
    TASK = "task"
    BUG = "bug"


class Status(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    VERIFYING = "verifying"
    DONE = "done"


class Priority(StrEnum):
    HIGHEST = "highest"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    LOWEST = "lowest"


class SprintStatus(StrEnum):
    PLANNING = "planning"
    ACTIVE = "active"
    COMPLETED = "completed"


ALL_TYPES = [ItemType.TASK, ItemType.BUG]
ALL_SPRINT_STATUSES = [SprintStatus.PLANNING, SprintStatus.ACTIVE, SprintStatus.COMPLETED]
ALL_STATUSES = [Status.BACKLOG, Status.TODO, Status.IN_PROGRESS,
                Status.IN_REVIEW, Status.VERIFYING, Status.DONE]
ALL_PRIORITIES = [Priority.HIGHEST, Priority.HIGH, Priority.MEDIUM,
                  Priority.LOW, Priority.LOWEST]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str | None] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Epic(Base):
    __tablename__ = "epics"
    __table_args__ = (
        CheckConstraint(
            "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
            name="ck_epics_status",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=Status.BACKLOG)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (
        CheckConstraint(
            "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
            name="ck_stories_status",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epic_id: Mapped[int] = mapped_column(ForeignKey("epics.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=Status.BACKLOG)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Sprint(Base):
    __tablename__ = "sprints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planning','active','completed')",
            name="ck_sprints_status",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=SprintStatus.PLANNING)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("type IN ('task','bug')", name="ck_tasks_type"),
        CheckConstraint(
            "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('highest','high','medium','low','lowest')",
            name="ck_tasks_priority",
        ),
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
    source_spec_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )  # 由哪个任务的 spec 生成
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
