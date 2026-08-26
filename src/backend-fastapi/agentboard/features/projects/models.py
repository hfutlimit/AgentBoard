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
    # Worker 认领租约（Epic 96 P2-0 同款，2026-08-26 补齐 Story 侧）：
    # claim_story 成功时写入；unclaim/complete/回收时清空。
    # 空串/NULL = 非 worker 持有（用户手工置 todo 的行不受租约回收影响）。
    # 判定回收必须用 claimed_at —— updated_at 带 onupdate，任何无关写入都会刷新，
    # 会把崩溃 Worker 的租约无限续期（与 proposals 迁移 i5j6k7l8m9n0 同因）。
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True, server_default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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

    # ---- Serialization -------------------------------------------------
    # ``to_public_dict`` 给常规业务端点（list/get）用：脱敏敏感配置（CLI 模板、
    # auth_key 指纹、probe 诊断），避免 ``_ser`` 表全列透传到前端。
    # ``_ser``（service_helpers）保留作内部 / admin 调试用。
    #
    # 2026-08-20 Epic 151 Story 326 Task 1297：档 A 阻断级修复 —
    # MembersTab 标榜「全局 Agent 池」，但 ``/api/agents`` 原返回全列（含
    # ``cli_command`` / ``auth_key`` / ``probe_message``），无 project 过滤，
    # 任意登录用户可拉全表。修复=脱敏 + 软鉴权（``_auth_is_required`` 时要求登录）。
    _PUBLIC_FIELDS = (
        "id", "agent_id", "name", "roles", "capabilities",
        "model", "online", "enabled",
        "last_heartbeat", "last_probe_at", "created_at", "updated_at",
    )

    def to_public_dict(self) -> dict:
        """返回业务端点用的脱敏 dict（无敏感配置）。"""
        from ...core.service_helpers import _ser  # 避免循环 import
        full = _ser(self) or {}
        return {k: full.get(k) for k in self._PUBLIC_FIELDS}

    def to_admin_dict(self) -> dict:
        """返回 admin / owner 可见的全字段 dict（含 ``cli_command`` / ``auth_key`` /
        ``probe_message`` / ``user_id``）。仅写接口（register/update/probe）的
        人类 admin 调用方可用；Agent 自调用（heartbeat/deregister）仍走
        :meth:`to_public_dict`。

        2026-08-20 Epic 151 Story 326 Task 1297a：闭合 5 个写接口字段暴露面。
        """
        from ...core.service_helpers import _ser  # 避免循环 import
        return _ser(self) or {}


class Worker(Base):
    """Worker 机器身份（2026-08-26 P1 修复：多 Worker 部署隔离）。

    一个 Worker 是一台物理/虚拟机器，跑一个 ``agentboard.worker`` 进程，
    负责本机的 ``AgentInstance`` 探测与上报。``worker_id`` 是外部自报
    唯一标识（与 ``Agent.agent_id`` 同风格；不绑 user_id —— Worker
    跨用户共享，不属于任何单用户）。
    """

    __tablename__ = "workers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    def to_public_dict(self) -> dict:
        from ...core.service_helpers import _ser
        return _ser(self) or {}


class AgentInstance(Base):
    """Agent 在某个 Worker 上的可执行实例（2026-08-26 P1 修复）。

    ``(worker_id, agent_id)`` 唯一：同一逻辑 agent 在不同 worker 上有不同
    CLI 模板（如 Worker A 的 ``codex --flag-a`` vs Worker B 的 ``codex --flag-b``）。
    本机的 ``cli_command`` / ``online`` / ``probe_message`` 都挂这里，**不再**
    放 ``Agent`` 表 —— 多 Worker 部署时各 Worker 互不影响。

    ``auth_key`` 本机凭据：``to_public_dict`` 脱敏（与 ``Agent`` 一致）。
    ``cli_command`` 在 owner 视角（按 ``worker_id`` 过滤）下保留 —— Worker
    自调用需要本机 CLI 模板。
    """

    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint("worker_id", "agent_id", name="uq_agent_instance_worker_agent"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker_id: Mapped[str] = mapped_column(
        ForeignKey("workers.worker_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cli_command: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    auth_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    probe_message: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # Owner 视角：Worker 是 owner，需要本机 CLI 模板。
    # 但 ``auth_key`` 仍脱敏（即使是 owner 也不在 JSON 里走明文）。
    _OWNER_FIELDS = (
        "id", "worker_id", "agent_id", "cli_command", "model",
        "enabled", "online", "last_heartbeat", "last_probe_at",
        "probe_message", "created_at", "updated_at",
    )
    # 跨 worker 视角：脱敏 cli_command（避免一个 Worker 看到另一个 Worker 的命令）
    _CROSS_FIELDS = (
        "id", "worker_id", "agent_id", "model",
        "enabled", "online", "last_heartbeat", "last_probe_at",
        "last_probe_at", "created_at", "updated_at",
    )

    def to_owner_dict(self) -> dict:
        """Owner 视角 dict（Worker 调本机 /admin 视角），含 ``cli_command``，脱敏 ``auth_key``。"""
        from ...core.service_helpers import _ser
        full = _ser(self) or {}
        return {k: full.get(k) for k in self._OWNER_FIELDS}

    def to_public_dict(self) -> dict:
        """跨 worker 视角 dict（agent 列表等通用接口），脱敏 ``cli_command`` / ``auth_key``。"""
        from ...core.service_helpers import _ser
        full = _ser(self) or {}
        return {k: full.get(k) for k in self._CROSS_FIELDS}


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
