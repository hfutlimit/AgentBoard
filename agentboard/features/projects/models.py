from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.enums import SprintStatus
from ...core.common.models import Base, utc_now

# Story 状态集合（Ticket 全流程，2026-08-09）：不并入共用 Status 枚举，避免污染 Task 状态机。
# 8 值：backlog=创建默认；confirmed=用户确认要做（人工闸门，触发 agent 自动处理）；
# todo/in_progress/in_review/verifying/done=常规执行流；blocked=异常态（无 previous_status 恢复）。
# 评审职责已整体下沉 Task 层（design task 的 in_design 评审流 + 实现 task 的 in_review 评审）。
STORY_STATUSES = {"backlog", "confirmed", "todo", "in_progress", "in_review",
                  "verifying", "done", "blocked"}
STORY_REVIEW_STATUSES = set()  # 历史兼容占位：Story 级评审态已下线（恒空）

# Story 状态迁移表（与 service.py 顶部原 STORY_TRANSITIONS 一致）：
# 单步查表 + blocked 全向特判（set_story_status）。Story 无 previous_status
# （blocked 解除仅限 → todo/in_progress）。confirmed 是「用户确认要做」的人工
# 闸门态，由 confirm_story 专用入口触发（PATCH 亦可）。
STORY_TRANSITIONS: dict[str, set[str]] = {
    "backlog":     {"confirmed", "blocked"},
    "confirmed":   {"todo", "blocked"},
    "todo":        {"in_progress", "backlog", "blocked"},
    "in_progress": {"in_review", "todo", "blocked"},
    "in_review":   {"verifying", "done", "in_progress", "blocked"},
    "verifying":   {"done", "in_progress", "blocked"},
    "done":        {"in_progress", "todo", "blocked"},
    "blocked":     {"todo", "in_progress"},
}

STORY_STATUS_SQL = "status IN ('backlog','confirmed','todo','in_progress','in_review','verifying','done','blocked')"


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str | None] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_private: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # Story 137（项目中心）：归档机制。归档项目默认从列表隐藏，但保留数据可恢复。
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


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
    # Story 265：Epic 自身状态机保留 backlog（独立于 Task 5 状态集）
    status: Mapped[str] = mapped_column(String(40), default="backlog")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (CheckConstraint(STORY_STATUS_SQL, name="ck_stories_status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epic_id: Mapped[int] = mapped_column(ForeignKey("epics.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # Story 265：Story 自身状态机保留 backlog/confirmed 等（独立于 Task 5 状态集）
    status: Mapped[str] = mapped_column(String(40), default="backlog")
    # Epic 123：是否需要设计评审段（true=走 in_design→design_pending_review→design_review_approved；
    # false=todo 直接进 in_progress 快速流）。默认 true。
    needs_design: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Epic 122 S1：评审闭环
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    review_round: Mapped[int] = mapped_column(Integer, default=0)
    # Epic 130：是否进入项目看板（ticket「进入 kanban」标记，标记后 worker 自动化处理）
    in_kanban: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class StoryStatusHistory(Base):
    """Story 状态变更历史（Ticket 全流程，2026-08-09）。

    与 task_status_history 同构：每次状态变更追加一条，可追溯/审计。
    changed_by 为操作人 user_id（系统操作可空）；reason 记录变更原因/备注。
    """

    __tablename__ = "story_status_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(40), nullable=False)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ReviewVote(Base):
    """评审投票（Epic 122 S3 M3 多数决）：一实体（Story/Task）多评审人各一票。

    - 一人一票：UNIQUE(entity_type, entity_id, reviewer_user_id)，改票走 upsert；
    - verdict：approve | reject；评论是评审意见载体（comment_id 关联评论）；
    - round：所属评审轮次（驳回结算后历史票清空，开新一轮）。
    """

    __tablename__ = "review_votes"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "reviewer_user_id",
            name="uq_review_votes_entity_reviewer",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)  # story | task
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(10), nullable=False)  # approve | reject
    comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id"), nullable=True
    )
    round: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


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
    # JSON list.  Legacy string tags and structured capability entries are
    # normalized by the scheduling service before persistence.
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cli_command: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 2026-08-09（Agent 配置中心化）：cli_command 支持 {model} 占位符，
    # 同一 CLI 多 agent 各自注入模型（如 codebuddy 建 hy3 / deepseek-v4-flash 两个 agent）
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    auth_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Worker probe 结果（前端实时展示）：probe_message 如 "OK v1.2.3" / "超时 8s" / "命令不存在"
    probe_message: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
