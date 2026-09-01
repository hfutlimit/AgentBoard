from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.enums import ItemType, Priority, Status
from ...core.common.models import Base, utc_now


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Story 265：type 收敛为 4 值（dev/bug/qa/design），原 task/test_execution 已迁移
        CheckConstraint("type IN ('dev','bug','qa','design')", name="ck_tasks_type"),
        # Story 265：status 收敛为 5 值（todo/in_progress/in_review/done/blocked）
        CheckConstraint("status IN ('todo','in_progress','in_review','done','blocked')", name="ck_tasks_status"),
        CheckConstraint("priority IN ('highest','high','medium','low','lowest')", name="ck_tasks_priority"),
        CheckConstraint(
            "complexity IS NULL OR (complexity >= 1 AND complexity <= 5)",
            name="ck_tasks_complexity",
        ),
        CheckConstraint(
            "assignment_mode IN ('claim','arbitrated')",
            name="ck_tasks_assignment_mode",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"), nullable=True, index=True)
    sprint_id: Mapped[int | None] = mapped_column(ForeignKey("sprints.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(10), default=ItemType.DEV)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default=Status.TODO)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM)
    # PR-6（P0-5 修复）：design task 完成后是否需要 user 确认才进 done。
    # True → submit-review 跳过自动 reviewer 分配，state 保持 in_review
    # 但等的是 user（POST /api/tasks/{id}/user_confirm），不是 reviewer。
    # False（默认）→ 走原 agent review 流（向后兼容 legacy）。
    # type='design' 的 task 创建时 service 显式置 True。
    needs_human_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0",
    )
    # Story 265：状态原因枚举（done 必填 completed/withdrawn；blocked 必填 4 选 1）
    status_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 调度暂缓原因不是终态 status_reason；成功分配时清空。
    assignment_deferred_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignment_deferred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[str] = mapped_column(Text, default="")
    source_spec_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    # Epic 17: 任务管理增强
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # 归属收敛（2026-09-01）：task 的 owner（创建者 user）+ 创建方 agent。
    # 处理（执行/评审/认领/派发）只允许 processing Agent.user_id ==
    # created_by_user_id；created_by_user_id 为 NULL（存量）时 fail closed，
    # 需人工补 owner 才能被处理。见 docs/design/agent-ownership-scoping-plan.md。
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    created_by_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    labels: Mapped[str] = mapped_column(Text, default="[]")  # JSON array string
    # Epic 32 Story 49.3: 看板卡片显示预估时间（工时，单位小时）
    estimate: Mapped[float | None] = mapped_column(nullable=True)
    # Capability/matching profile.  JSON values are stored as text for equal
    # SQLite/MariaDB behavior and normalized at service boundaries.
    needed_capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    complexity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    assignment_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="claim"
    )
    current_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_assignments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Epic 122 S2 M2: Task 评审闭环（reviewer 指派 + 评审轮次护栏）
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # 归属收敛（2026-09-01）：评审也限 owner 的 agent。reviewer_id 仍是
    # users.id（一人一票/鉴权兼容），reviewer_agent_id 记录被指派的
    # 具体评审 Agent（≠ 实现方 agent），用于把 review 工作路由到正确的
    # worker 队列（同 owner 多 agent 时按 user 反查会路由错人）。
    reviewer_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_round: Mapped[int] = mapped_column(Integer, default=0)
    # 状态扩展（Epic 123）：进入 blocked 时记录上一个状态，解除时恢复
    previous_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Worker 认领租约（2026-08-26）：仅 claim_development_task 写入；
    # 人工 set_status/apply/arbitrate 路径不写 → 回收只影响 agent 认领的行。
    # in_progress 是人机共享状态，回收额外要求 updated_at < cutoff：
    # 认领后无任何后续写入才视为「持有者已死」，评审驳回等近期活动一律保护。
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True, server_default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class TaskStatusHistory(Base):
    """任务状态变更历史（Epic 123）：每次状态变更追加一条，可追溯/审计。

    - 覆盖 set_status、claim、submit-review、review 等全部状态变更路径；
    - changed_by 为操作人 user_id（系统操作可空）；reason 记录变更原因/备注。
    """

    __tablename__ = "task_status_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(40), nullable=False)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Comment(Base):
    """评论：可挂在 Task / Story / Epic 三种实体上，三者恰好其一非空。"""
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"), nullable=True, index=True)
    epic_id: Mapped[int | None] = mapped_column(ForeignKey("epics.id"), nullable=True, index=True)
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


# Attachment 配置常量（从原 service.py 行 1434-1436 搬迁）
import os as _os
ATTACHMENT_DIR = _os.getenv("AGENTBOARD_ATTACHMENT_DIR", "data/attachments")
ATTACHMENT_MAX_SIZE = int(_os.getenv("AGENTBOARD_ATTACHMENT_MAX_SIZE", str(10 * 1024 * 1024)))  # 10 MB
ATTACHMENT_ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf",
    "text/plain", "text/markdown", "text/csv",
    "application/json",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

