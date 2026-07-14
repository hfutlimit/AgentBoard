from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text
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
    # Epic 17: 任务管理增强
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    labels: Mapped[str] = mapped_column(Text, default="[]")  # JSON array string
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


class AuditLog(Base):
    """Epic 22 Story 22.1: API操作审计日志"""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)  # project/epic/story/task
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # GET/POST/PUT/PATCH/DELETE
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)  # 脱敏后的请求体
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class TaskDependency(Base):
    """Epic 22 Story 22.2: 任务依赖关系"""
    __tablename__ = "task_dependencies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    dependency_type: Mapped[str] = mapped_column(String(20), default="blocks")  # blocks / blocked_by / relates_to
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (
        # 防止重复依赖
        # SQLite 不支持带条件的 UNIQUE 约束，放到 DB 层面处理
    )


class WebhookConfig(Base):
    """Epic 22 Story 22.4: Webhook 配置"""
    __tablename__ = "webhook_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    events: Mapped[str] = mapped_column(Text, default="[]")  # JSON: ["task.created","task.status_changed",...]
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
