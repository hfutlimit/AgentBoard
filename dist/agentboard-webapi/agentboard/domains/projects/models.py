from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..common.enums import SprintStatus, Status
from ..common.models import Base, utc_now


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str | None] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_private: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Epic(Base):
    __tablename__ = "epics"
    __table_args__ = (CheckConstraint(
        "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
        name="ck_epics_status",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=Status.BACKLOG)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (CheckConstraint(
        "status IN ('backlog','todo','in_progress','in_review','verifying','done')",
        name="ck_stories_status",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epic_id: Mapped[int] = mapped_column(ForeignKey("epics.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=Status.BACKLOG)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (CheckConstraint("role IN ('owner','member')", name="ck_members_role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Sprint(Base):
    __tablename__ = "sprints"
    __table_args__ = (CheckConstraint(
        "status IN ('planning','active','completed')", name="ck_sprints_status",
    ),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=SprintStatus.PLANNING)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
