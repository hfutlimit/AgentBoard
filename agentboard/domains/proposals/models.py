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

from ..common.models import Base, utc_now


class ProposalStatus(StrEnum):
    DRAFT = "draft"                # 用户编辑中，尚未派发
    QUEUED = "queued"              # 已入队，等待 Worker 认领
    ANALYZING = "analyzing"        # Agent 正在分析并生成问题
    AWAITING = "awaiting"          # 已产出问题，等待用户作答
    ANSWERED = "answered"          # 用户已作答，等待下一轮分析或收敛
    CONVERGED = "converged"        # 澄清收敛，等待人工终审
    STORY_CREATED = "story_created"  # 已转化为 Story（终态）
    FAILED = "failed"              # 分析失败 / 超时（可回退重投）


ALL_PROPOSAL_STATUSES = [
    ProposalStatus.DRAFT, ProposalStatus.QUEUED, ProposalStatus.ANALYZING,
    ProposalStatus.AWAITING, ProposalStatus.ANSWERED, ProposalStatus.CONVERGED,
    ProposalStatus.STORY_CREATED, ProposalStatus.FAILED,
]

# SQL CHECK 约束用的字面量（与上表保持一致，供 models / migration 共用）
_STATUS_SQL_LIST = "'" + "','".join(s.value for s in ALL_PROPOSAL_STATUSES) + "'"


# Proposal 澄清状态机（service.py 集中引用，参照 Task TRANSITIONS / DOCUMENT_TRANSITIONS 模式）
#
# 正常闭环：draft → queued → analyzing → awaiting → answered → analyzing(下一轮) → converged → story_created
# 异常回路：analyzing/awaiting/answered → failed → queued（重投）
PROPOSAL_TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.DRAFT: {ProposalStatus.QUEUED},
    ProposalStatus.QUEUED: {
        ProposalStatus.ANALYZING, ProposalStatus.DRAFT, ProposalStatus.FAILED,
    },
    ProposalStatus.ANALYZING: {
        ProposalStatus.AWAITING, ProposalStatus.CONVERGED,
        ProposalStatus.QUEUED,   # 超时回退，复用 DaemonScheduler
        ProposalStatus.FAILED,
    },
    ProposalStatus.AWAITING: {
        ProposalStatus.ANSWERED, ProposalStatus.CONVERGED, ProposalStatus.FAILED,
    },
    ProposalStatus.ANSWERED: {
        ProposalStatus.ANALYZING,  # 进入下一轮澄清
        ProposalStatus.CONVERGED,  # 用户主动跳过继续澄清
        ProposalStatus.FAILED,
    },
    ProposalStatus.CONVERGED: {
        ProposalStatus.STORY_CREATED,
        ProposalStatus.ANALYZING,  # 人工终审驳回，继续澄清
    },
    ProposalStatus.STORY_CREATED: set(),  # 终态
    ProposalStatus.FAILED: {ProposalStatus.QUEUED, ProposalStatus.DRAFT},
}

# 允许 Agent 在其中提问的状态（提问即产出一轮问题）
ASKABLE_STATUSES = {ProposalStatus.ANALYZING}

# 可被 Worker 原子认领进入 analyzing 的状态：
# queued = 首轮待分析；answered = 用户已作答，需进入下一轮澄清。
# 服务端 CAS 认领端点与 Worker 侧发现逻辑共用该集合，避免两处定义漂移。
CLAIMABLE_STATUSES = {ProposalStatus.QUEUED, ProposalStatus.ANSWERED}


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
    # 转化产出的 Story（P3 回填）
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 提出人；Worker 通过 proposal_id 反查项目与提出人以确定身份归属
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 失败原因（status=failed 时填充）
    error: Mapped[str] = mapped_column(Text, default="")
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
