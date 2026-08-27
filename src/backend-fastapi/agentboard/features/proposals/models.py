"""Proposal / ProposalRound / ProposalQuestion 实体（Epic 96：Proposal 澄清回路）。

人机协同需求分析的持久化基座：用户在 Web 端提交 Proposal（需求提案），服务端派发给
工作者机器上的无头 Agent 做需求澄清；Agent 以「轮次（Round）」为单位回写 open questions，
用户逐条作答，多轮收敛后由人工终审转化为 Story。

设计要点（不复用 Task.spec，独立三表）：
- ``proposals``：提案主体 + 状态机 + 收敛后的 converged_spec + 回填 story_id
- ``proposal_rounds``：一次澄清轮次，``(proposal_id, round_no)`` 唯一 —— 消息 at-least-once
  投递与 LLM 非确定性靠该唯一约束兜底防重投
- ``proposal_questions``：轮次下的单条问题与用户作答（支持「不确定」标记）

遵循 OpenSpec 增量式约束：不修改既有表结构，新增实体与端点。
存储双后端兼容（SQLite / MariaDB），迁移由 Alembic 管理。
"""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.models import Base, utc_now


class ProposalStatus(StrEnum):
    DRAFT = "draft"                # 用户编辑中，尚未派发（历史态，兼容存量数据）
    PENDING = "pending"            # 待开始：创建/编辑后停留，点击「开始 grill」才入队发消息
    QUEUED = "queued"              # 已入队，等待 Worker 认领
    ANALYZING = "analyzing"        # Agent 正在分析并生成问题
    AWAITING = "awaiting"          # 已产出问题，等待用户作答
    ANSWERED = "answered"          # 用户已作答，等待下一轮分析或收敛
    CONVERGED = "converged"        # 需求已明确（澄清收敛），等待生成 ticket
    STORY_CREATED = "story_created"  # 已转化为 Story（历史终态，兼容存量数据）
    TICKET_PREPARING = "ticket_preparing"  # 工单生成中（异步创建 ticket 的中间态）
    TICKET_CREATED = "ticket_created"      # 已生成工单（终态，泛化 story_created）
    FAILED = "failed"              # 分析失败 / 超时（可回退重投）
    CANCELLED = "cancelled"        # 问答完成前由用户取消（终态）


ALL_PROPOSAL_STATUSES = [
    ProposalStatus.DRAFT, ProposalStatus.PENDING, ProposalStatus.QUEUED,
    ProposalStatus.ANALYZING, ProposalStatus.AWAITING, ProposalStatus.ANSWERED,
    ProposalStatus.CONVERGED, ProposalStatus.STORY_CREATED,
    ProposalStatus.TICKET_PREPARING, ProposalStatus.TICKET_CREATED,
    ProposalStatus.FAILED, ProposalStatus.CANCELLED,
]

# SQL CHECK 约束用的字面量（与上表保持一致，供 models / migration 共用）
_STATUS_SQL_LIST = "'" + "','".join(s.value for s in ALL_PROPOSAL_STATUSES) + "'"


# Proposal 澄清状态机（service.py 集中引用，参照 Task TRANSITIONS / DOCUMENT_TRANSITIONS 模式）
#
# 正常闭环：pending → queued → analyzing → awaiting → answered → analyzing(下一轮)
#           → converged → ticket_preparing → ticket_created
# 异常回路：analyzing/awaiting/answered → failed → queued（重投）
# 编辑回退：非终态用户编辑正文 → 回 pending（已答历史保留，全量重放）
PROPOSAL_TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.DRAFT: {ProposalStatus.QUEUED, ProposalStatus.CANCELLED},
    ProposalStatus.PENDING: {
        ProposalStatus.QUEUED, ProposalStatus.CANCELLED,
    },  # 点击「开始 grill」→ 入队
    ProposalStatus.QUEUED: {
        ProposalStatus.ANALYZING, ProposalStatus.DRAFT, ProposalStatus.FAILED,
        ProposalStatus.PENDING,  # 用户编辑 → 回待开始
        ProposalStatus.CANCELLED,
    },
    ProposalStatus.ANALYZING: {
        ProposalStatus.AWAITING, ProposalStatus.CONVERGED,
        ProposalStatus.QUEUED,   # 超时回退，复用 DaemonScheduler
        ProposalStatus.FAILED,
        ProposalStatus.PENDING,  # 用户编辑 → 回待开始
        ProposalStatus.CANCELLED,
    },
    ProposalStatus.AWAITING: {
        ProposalStatus.ANSWERED, ProposalStatus.CONVERGED, ProposalStatus.FAILED,
        ProposalStatus.PENDING,
        ProposalStatus.CANCELLED,
    },
    ProposalStatus.ANSWERED: {
        ProposalStatus.ANALYZING,  # 进入下一轮澄清
        ProposalStatus.CONVERGED,  # 用户主动跳过继续澄清
        ProposalStatus.FAILED,
        ProposalStatus.PENDING,
        ProposalStatus.CANCELLED,
    },
    ProposalStatus.CONVERGED: {
        ProposalStatus.STORY_CREATED,       # 历史路径（P3 同步直建）
        ProposalStatus.TICKET_PREPARING,    # 新路径：点击「生成 ticket」→ 异步生成中
        ProposalStatus.ANALYZING,           # 人工终审驳回，继续澄清
        ProposalStatus.PENDING,             # 用户编辑 → 回待开始
    },
    ProposalStatus.STORY_CREATED: set(),  # 终态
    ProposalStatus.TICKET_PREPARING: {
        ProposalStatus.TICKET_CREATED,  # agent 经 MCP 创建成功
        ProposalStatus.CONVERGED,       # 失败回退：可重新生成
    },
    ProposalStatus.TICKET_CREATED: set(),  # 终态
    ProposalStatus.FAILED: {ProposalStatus.QUEUED, ProposalStatus.DRAFT,
                            ProposalStatus.PENDING, ProposalStatus.CANCELLED},
    ProposalStatus.CANCELLED: set(),
}

# 允许 Agent 在其中提问的状态（提问即产出一轮问题）
ASKABLE_STATUSES = {ProposalStatus.ANALYZING}

# auto_create_ticket 可修改的状态集合（Story 389）：收敛及建单阶段后锁定。
# 该集合同时是「允许取消」的状态集合（取消截止点为 grill 收敛前）。
AUTO_TICKET_MODIFIABLE_STATUSES = {
    ProposalStatus.DRAFT, ProposalStatus.PENDING, ProposalStatus.QUEUED,
    ProposalStatus.ANALYZING, ProposalStatus.AWAITING, ProposalStatus.ANSWERED,
    ProposalStatus.FAILED,
}

# 可被 Worker 原子认领进入 analyzing 的状态：
# queued = 首轮待分析；answered = 用户已作答，需进入下一轮澄清。
# 服务端 CAS 认领端点与 Worker 侧发现逻辑共用该集合，避免两处定义漂移。
CLAIMABLE_STATUSES = {ProposalStatus.QUEUED, ProposalStatus.ANSWERED}


# ---- Proposal → Ticket 异步转化（2026-08-08 确认，文档 #59）----
# 可生成的工单类型（task/bug 复用 tasks 表，type 字段区分）
TICKET_TYPES: frozenset[str] = frozenset({"epic", "story", "task", "bug"})
# auto 只是转换请求的「待 Agent 决策」类型，不是最终工单类型。
AUTO_TICKET_TYPE = "auto"
AUTO_RESOLVABLE_TICKET_TYPES: frozenset[str] = frozenset({"epic", "story", "task"})
TICKET_REQUEST_TYPES: frozenset[str] = TICKET_TYPES | {AUTO_TICKET_TYPE}

# 转换请求状态机：pending（等待 worker）→ processing（worker 认领执行中）
# → done（已生成，回填 ticket_id）/ failed（失败，proposal 回退 converged）
TICKET_REQUEST_PENDING = "pending"
TICKET_REQUEST_PROCESSING = "processing"
TICKET_REQUEST_DONE = "done"
TICKET_REQUEST_FAILED = "failed"
TICKET_REQUEST_STATUSES: frozenset[str] = frozenset({
    TICKET_REQUEST_PENDING, TICKET_REQUEST_PROCESSING,
    TICKET_REQUEST_DONE, TICKET_REQUEST_FAILED,
})
_TICKET_REQ_STATUS_SQL_LIST = "'" + "','".join(sorted(TICKET_REQUEST_STATUSES)) + "'"
_TICKET_TYPE_SQL_LIST = "'" + "','".join(sorted(TICKET_REQUEST_TYPES)) + "'"


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_SQL_LIST})", name="ck_proposals_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # 用户提交的原始需求正文（Markdown）
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(20), default=ProposalStatus.DRAFT, index=True,
    )
    # 当前澄清轮次（0 = 尚未开始澄清）
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    # 澄清收敛后的最终需求规格（供 P3 生成 Story/Task）
    converged_spec: Mapped[str] = mapped_column(Text, default="")
    # 转化产出的 Story（P3 回填；ticket_type=story 时的快捷字段，兼容旧查询）
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 通用工单回填（2026-08-08 文档 #59）：四类 ticket（epic/story/task/bug）
    # 统一记类型 + 实体 id。story 类 ticket 同时回填 story_id 以兼容既有查询。
    ticket_type: Mapped[str] = mapped_column(String(20), default="")
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 收敛后是否自动让 Agent 选择 epic/story/task 并创建工单。
    auto_create_ticket: Mapped[bool] = mapped_column(Boolean, default=False)
    # 提出人；Worker 通过 proposal_id 反查项目与提出人以确定身份归属
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 失败原因（status=failed 时填充）
    error: Mapped[str] = mapped_column(Text, default="")
    # 「Agent 不可用」自动重投计数（2026-08-09）：后端 job 每自动重投一次 +1，
    # 达到上限（默认 5）停投转人工。提案进入成功终态时清零。独立字段而非编码进
    # error 文本——worker 每次失败都会用新错误覆盖 error，文本计数会丢失。
    auto_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # --- 认领租约（P2-0）---
    # 当前持有 analyzing 租约的 Worker 服务账号名，空串表示无人持有。
    claimed_by: Mapped[str] = mapped_column(String(100), default="")
    # 租约起算时刻。**只在认领成功时写入**，与 updated_at 严格区分：
    # updated_at 带 onupdate，任何无关写入（用户作答 / PATCH converged_spec / 补写轮次）
    # 都会刷新它 —— 若用它判定租约，崩溃 Worker 的租约会被旁人写操作不断续期，
    # 提案永久卡死在 analyzing。故租约必须挂在这个独立、只由认领动作推进的字段上。
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProposalRound(Base):
    """一次澄清轮次。``(proposal_id, round_no)`` 唯一 —— 重投消息天然幂等。"""

    __tablename__ = "proposal_rounds"
    __table_args__ = (
        UniqueConstraint("proposal_id", "round_no", name="uq_proposal_rounds_proposal_round"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # Agent 对本轮的说明 / 分析摘要
    summary: Mapped[str] = mapped_column(Text, default="")
    # 产出本轮问题的 Agent 账号名（Worker 服务账号）
    agent: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProposalQuestion(Base):
    """轮次下的单条 open question 与用户作答。"""

    __tablename__ = "proposal_questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    round_id: Mapped[int] = mapped_column(
        ForeignKey("proposal_rounds.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # 轮内序号，用于前端稳定排序
    seq: Mapped[int] = mapped_column(Integer, default=0)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="")
    # 用户标记「暂不确定」：视为已处理但不提供答案，交由 Agent 自行假设
    unsure: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProposalTicketRequest(Base):
    """Proposal → Ticket 转换请求（2026-08-08 文档 #59，异步生成链路）。

    ``(proposal_id, type)`` 唯一：同一提案同一类型至多一个请求，重复提交幂等复用
    （消息 at-least-once / 前端重复点击都不产生重复 ticket）。

    status 流转：pending（等待 worker）→ processing（worker 认领执行中）
    → done（agent 经 MCP 创建成功，回填 ticket_id）/ failed（失败，proposal
    回退 converged 可重试）。
    """

    __tablename__ = "proposal_ticket_requests"
    __table_args__ = (
        UniqueConstraint("proposal_id", "type", name="uq_ticket_req_proposal_type"),
        CheckConstraint(
            f"type IN ({_TICKET_TYPE_SQL_LIST})", name="ck_ticket_req_type",
        ),
        CheckConstraint(
            f"status IN ({_TICKET_REQ_STATUS_SQL_LIST})", name="ck_ticket_req_status",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # 请求类型：epic / story / task / bug / auto。auto 由 Agent 决策最终类型。
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # auto 请求的最终类型；手动请求保持空字符串。
    resolved_type: Mapped[str] = mapped_column(String(20), default="")
    # 层级父级：epic 无父；story 必挂 epic；task/bug 必挂 epic + story
    parent_epic_id: Mapped[int | None] = mapped_column(
        ForeignKey("epics.id", ondelete="SET NULL"), nullable=True,
    )
    parent_story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True,
    )
    # 标题覆盖（省略用提案标题）
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(
        String(20), default=TICKET_REQUEST_PENDING, index=True,
    )
    # 创建成功的实体 id（epic/story/task 各自表），幂等回填
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 失败原因（status=failed 时填充）
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


# Proposal 状态机的 claim 租约过期秒数(用于 reclaim_stale_proposals 等)
DEFAULT_CLAIM_LEASE_SECONDS = 1800
