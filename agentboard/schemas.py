"""Pydantic schemas for AgentBoard REST API.

Phase 5 重构：从 api.py 抽出的所有 BaseModel 集中地,供 routers/api.py 共同 import。
以 "from __future__ import annotations" + `from .schemas import *` 形式被
各 router 使用,解决跨模块 ForwardRef 解析问题(Pydantic 找不到类的报错)。
"""
from __future__ import annotations
import re
from pydantic import BaseModel, Field, field_validator

from . import service  # for DEFAULT_REVIEW_TIMEOUT_MINUTES / DEFAULT_TIMEOUT_SCAN_BATCH used by ReassignTimeoutIn
from .api_helpers import _PERMISSION_RE  # for ApiKeyCreate.validate_permissions


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str | None = None


class EpicIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class EpicPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None


class StoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    # Epic 123：是否需要设计评审段（默认 true 走设计评审流）
    needs_design: bool = True


class StoryPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None
    needs_design: bool | None = None
    # Epic 130: 是否进入项目看板（ticket「进入 kanban」标记）
    in_kanban: bool | None = None


class AgentRegisterIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    roles: str = "[]"
    capabilities: str | list[str | dict] = "[]"
    cli_command: str = ""
    model: str = ""
    auth_key: str = ""


class AgentUpdateIn(BaseModel):
    """前端 Agent 配置中心（PUT /api/agents/{agent_id}，全字段可选）。"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    roles: str | None = None
    capabilities: str | list[str | dict] | None = None
    cli_command: str | None = None
    model: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    user_id: int | None = None


class AgentHeartbeatIn(BaseModel):
    """Worker probe 上报（可选 body）：probe_ok=False 表示探测失败（置 offline）。"""
    probe_ok: bool | None = None
    probe_message: str = ""


class AgentProbeIn(BaseModel):
    """手动 probe 覆盖（POST /api/agents/{agent_id}/probe，可选）。"""
    timeout: int = Field(default=8, ge=1, le=30)


class AgentReviewIn(BaseModel):
    verdict: str = Field(pattern="^(approve|reject)$")
    comment: str = Field(min_length=1, max_length=2000)


class ReassignTimeoutIn(BaseModel):
    timeout_minutes: int = Field(default=service.DEFAULT_REVIEW_TIMEOUT_MINUTES, ge=1, le=1440)
    max_per_run: int = Field(default=service.DEFAULT_TIMEOUT_SCAN_BATCH, ge=1, le=200)


class TaskIn(BaseModel):
    project_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    # Story 265：type 默认值由 task → dev（4 值枚举）
    type: str = "dev"
    description: str = ""
    spec: str = ""
    priority: str = "medium"
    # Epic 17: 任务管理增强
    assignee_id: int | None = None
    due_date: str | None = None  # ISO date string YYYY-MM-DD
    labels: str = "[]"  # JSON array string
    # Epic 32 Story 49.3: 预估工时（小时）
    estimate: float | None = None
    needed_capabilities: str | list[str | dict] = "[]"
    complexity: int | None = Field(None, ge=1, le=5)
    domain_tags: str | list[str] = "[]"
    assignment_mode: str = Field("claim", pattern=r"^(claim|arbitrated)$")


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    type: str | None = None
    status: str | None = None
    description: str | None = None
    spec: str | None = None
    priority: str | None = None
    sprint_id: int | None = None
    # Epic 17: 任务管理增强
    assignee_id: int | None = None
    due_date: str | None = None  # ISO date string YYYY-MM-DD
    labels: str | None = None  # JSON array string
    # Epic 32 Story 49.3: 预估工时（小时）
    estimate: float | None = None
    needed_capabilities: str | list[str | dict] | None = None
    complexity: int | None = Field(None, ge=1, le=5)
    domain_tags: str | list[str] | None = None
    assignment_mode: str | None = Field(None, pattern=r"^(claim|arbitrated)$")
    # Story 265: 状态原因（done/blocked 必填，经状态机校验）
    status_reason: str | None = None


class CommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class StatusIn(BaseModel):
    status: str
    # Epic 123：状态变更原因/备注（写入 task_status_history.reason）
    reason: str = ""
    # Story 265：状态原因枚举（done/blocked 必填，其他状态忽略）
    status_reason: str | None = None


class SpecAppendIn(BaseModel):
    text: str = Field(min_length=1)


class AuthRegister(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("username is required")
        return value


class AuthLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SprintIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    goal: str = ""
    start_date: str | None = None
    end_date: str | None = None


class SprintPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    goal: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ScheduleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    schedule_type: str = "cron"
    cron_expr: str | None = None
    # Story 106：绑定松绑（agent / 固定 task / 可选筛选，全部可选）
    agent: str | None = None
    task_id: int | None = None
    task_priority: str | None = None
    task_type: str | None = None
    epic_id: int | None = None


class SchedulePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    schedule_type: str | None = None
    cron_expr: str | None = None
    enabled: bool | None = None
    next_run_at: str | None = None
    # Story 106：显式 null = 解除绑定 / 清除筛选
    agent: str | None = None
    task_id: int | None = None
    task_priority: str | None = None
    task_type: str | None = None
    epic_id: int | None = None


class RunIn(BaseModel):
    task_id: int | None = None
    idempotency_key: str | None = Field(None, max_length=128)


class RunPatch(BaseModel):
    status: str | None = None
    output: str | None = None
    error_message: str | None = None
    summary: str | None = None
    log_ref: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    task_id: int | None = None


class RunReportIn(BaseModel):
    """Agent 主动报告 run 最终结果（Story 104）"""
    status: str
    summary: str | None = None
    log_ref: str | None = None


class ProjectPatchExtended(BaseModel):
    """Project PATCH 支持 is_private + is_archived（Story 137 项目中心）"""
    name: str | None = Field(None, min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str | None = None
    is_private: bool | None = None
    is_archived: bool | None = None


class MemberRoleIn(BaseModel):
    role: str = Field(..., pattern=r"^(owner|member)$")


class NotificationIn(BaseModel):
    user_id: int = Field(gt=0)
    notif_type: str = Field(..., pattern=r"^(project_invite|join_request|task_assigned|status_changed|mentioned)$")
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    link: str | None = Field(None, max_length=500)


class UserAdminPatch(BaseModel):
    is_admin: bool


class UserProfilePatch(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=254)
    avatar_url: str | None = Field(None, max_length=500)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("invalid email address")
        return normalized

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        if not re.fullmatch(r"https?://[^\s]+", value.strip()):
            raise ValueError("avatar_url must be an http(s) URL")
        return value.strip()


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1000)
    new_password: str = Field(min_length=8, max_length=1000)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=lambda: ["api:read"], max_length=100)
    agent_ref: str | None = Field(None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name is required")
        return value.strip()

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if any(len(p) > 120 or not _PERMISSION_RE.fullmatch(p) for p in normalized):
            raise ValueError("permissions must be namespaced strings such as 'mcp:tools:read'")
        return normalized


class ApiKeyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    enabled: bool | None = None
    permissions: list[str] | None = Field(None, max_length=100)
    agent_ref: str | None = Field(None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("name is required")
        return value.strip() if value is not None else None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str] | None) -> list[str] | None:
        return ApiKeyCreate.validate_permissions(value) if value is not None else None


class BulkTaskUpdate(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=100)
    status: str | None = None
    # Story 265：批量改 status 时可传 status_reason（done/blocked 必填）
    status_reason: str | None = None
    priority: str | None = None
    sprint_id: int | None = None
    # v3.0 批量指派：新增 assignee_id / clear_assignee（增量字段，向后兼容）
    assignee_id: int | None = None
    clear_assignee: bool = False
    # v3.2 批量改截止日期：新增 due_date / clear_due_date（增量字段，向后兼容）
    due_date: str | None = None
    clear_due_date: bool = False

    @field_validator("task_ids")
    @classmethod
    def validate_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("task_ids cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("task_ids must be unique")
        return value


class BulkTaskDelete(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=100)

    @field_validator("task_ids")
    @classmethod
    def validate_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("task_ids cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("task_ids must be unique")
        return value


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)
    secret: str | None = Field(None, max_length=256)
    events: list[str] = Field(default_factory=list)


class DocumentIn(BaseModel):
    project_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    type: str = "plan"  # memory / plan / knowledge / design
    status: str = "draft"  # draft / in_review / approved / cancelled
    epic_id: int | None = None
    story_id: int | None = None
    folder_id: int | None = None
    author_id: int | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    content: str | None = None
    type: str | None = None
    status: str | None = None
    folder_id: int | None = None  # null = 移出文件夹到根目录
    epic_id: int | None = None   # null = 清空 epic 关联（须属于文档项目）
    story_id: int | None = None  # null = 清空 story 关联（须属于文档项目/所属 epic）


class DocumentFolderIn(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    parent_id: int | None = None


class DocumentFolderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=300)
    parent_id: int | None = None  # null = 移动到根目录


class DocumentCommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)
    author_id: int | None = None


class DocumentCommentPatch(BaseModel):
    content: str = Field(min_length=1)
    author: str = Field(min_length=1, max_length=100)


class DocumentRevisionSaveIn(BaseModel):
    expected_revision_number: int
    title: str | None = None
    content: str | None = None
    change_note: str = ""
    author: str | None = None
    author_id: int | None = None


class DocumentRevisionRestoreIn(BaseModel):
    revision_number: int
    change_note: str
    author: str | None = None
    author_id: int | None = None


class ProposalIn(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    author_id: int | None = None


class ProposalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = None
    converged_spec: str | None = None
    story_id: int | None = None


class ProposalStatusIn(BaseModel):
    status: str
    error: str | None = None


class ProposalClaimIn(BaseModel):
    """Worker 原子认领提案。agent 为服务账号名，仅用于排障与轮次署名。"""

    agent: str = ""


class ProposalReclaimIn(BaseModel):
    """回收租约过期的 analyzing 提案。省略 lease_seconds 时用服务端默认值。"""

    lease_seconds: int | None = Field(default=None, ge=0)


class RecoverFailedIn(BaseModel):
    """Agent 不可用导致的 failed 提案自动重投参数（后端 job）。"""

    window_seconds: int | None = Field(default=None, ge=0)
    max_retries: int | None = Field(default=None, ge=1)


class ProposalAskIn(BaseModel):
    """Agent 回写一轮 open questions。round 省略时自动取下一轮。"""

    questions: list[str] = Field(min_length=1)
    round: int | None = None
    summary: str = ""
    agent: str = ""


class ProposalAnswerIn(BaseModel):
    answer: str = ""
    unsure: bool = False


class ProposalConvertIn(BaseModel):
    """人工终审确认：把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    epic_id 必填（目标 Epic 必须属于提案所在项目）；title 可覆盖 Story 标题，
    省略时用提案标题。
    """

    epic_id: int
    title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketRequestSpec(BaseModel):
    """创建/执行 Proposal → Ticket 转换请求的统一定义（2026-08-10 review）。

    type: epic / story / task / bug；
    - epic 独立，无需父级；
    - story 必填 epic_id；
    - task / bug 必填 epic_id + story_id。

    合并自旧 ProposalTicketIn（创建）+ TicketRequestExecuteIn（execute-by-type），
    两份字段完全同构。旧名保留为 alias，1 release 后下架。
    """

    type: str
    epic_id: int | None = None
    story_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketRequestExecuteSpec(BaseModel):
    """``POST /api/ticket-requests:execute`` 的 body（2026-08-10 URL 命名统一）。

    RPC 命名空间端点不再把 proposal 塞进 URL，改为 body 携带
    ``proposal_id`` + 层级字段（语义同旧 ``execute-by-type``）。
    """

    proposal_id: int
    type: str
    epic_id: int | None = None
    story_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketFailIn(BaseModel):
    error: str = ""


class TicketReclaimIn(BaseModel):
    lease_seconds: int | None = None


# 兼容旧 import（pre-existing 客户端/MCP 工具可能仍引用）
# 1 release 后下架——届时新代码全部用 TicketRequestSpec。
ProposalTicketIn = TicketRequestSpec
TicketRequestExecuteIn = TicketRequestSpec
