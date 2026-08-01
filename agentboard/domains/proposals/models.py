from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..common.models import Base, utc_now


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','queued','analyzing','awaiting','answered','converged','story_created','failed')",
            name="ck_proposals_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    converged_spec: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    max_rounds: Mapped[int] = mapped_column(Integer, default=5)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str] = mapped_column(String(100), default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProposalRound(Base):
    __tablename__ = "proposal_rounds"
    __table_args__ = (
        UniqueConstraint("proposal_id", "round_number", name="uq_proposal_round_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    agent: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ProposalQuestion(Base):
    __tablename__ = "proposal_questions"
    __table_args__ = (
        CheckConstraint("status IN ('open','answered')", name="ck_proposal_questions_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_id: Mapped[int] = mapped_column(
        ForeignKey("proposal_rounds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="")
    unsure: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
