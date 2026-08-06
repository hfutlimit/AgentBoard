from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..common.enums import SprintStatus, Status
from ..common.models import Base, utc_now

# Story 评审闭环新增状态（Epic 122 S1）：不并入共用 Status 枚举，避免污染 Task 状态机。
# 仅 Story 使用；pending_review=待评审，ready=评审通过可进入开发。
STORY_REVIEW_STATUSES = {"pending_review", "ready"}

STORY_STATUS_SQL = "status IN ('backlog','todo','in_progress','in_review','verifying','done','pending_review','ready','blocked')"


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
    __table_args__ = (CheckConstraint(STORY_STATUS_SQL, name="ck_stories_status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epic_id: Mapped[int] = mapped_column(ForeignKey("epics.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=Status.BACKLOG)
    # Epic 122 S1：评审闭环
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    review_round: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Agent(Base):
    """Agent 注册表（Epic 122 S1）：外部 Agent 自报身份，参与 Story 评审等协作闭环。

    agent_id 为外部 Agent 自报标识（如 ``wb-dev-1``），幂等注册的唯一键；
    user_id 绑定服务账号用户（经 ProjectMember 授权，复用既有项目权限模型）。
    """

    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    roles: Mapped[str] = mapped_column(String(200), nullable=False, default="[]")
    capabilities: Mapped[str] = mapped_column(String(500), nullable=False, default="[]")
    cli_command: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    auth_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


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
