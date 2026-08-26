"""????????Epic 140 & Configurable Behavior Learning??

?? 1 task_outcome??????done/blocked/withdrawn??????????????
- score: 0~1 ???
- judge_json: ?????

?? 3 Episode RAG + Playbook?Worker ?????Story 268??
- episode_embedding???????? run trace ?????? task recall ?????? prompt?
- project_playbook???? playbook ????
- project_playbook_episode??? episode ?? entry?

Configurable Behavior Learning?
- Learning???????????accepted_review_feedback, review_judgment_reversal, qa_defect ???
"""
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.models import Base, utc_now

ALL_PLAYBOOK_OUTCOMES = ("success", "failure")


class TaskOutcome(Base):
    __tablename__ = "task_outcome"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="ck_task_outcome_score"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    agent_registry_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_assignments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(10), default="dev")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    judge_json: Mapped[str] = mapped_column(Text, default="{}")
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class EpisodeEmbedding(Base):
    """?? run trace ???????Story 268 ?? 3??"""

    __tablename__ = "episode_embedding"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"), unique=True, index=True, comment="?? id?????? episode?"
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(10), default="dev")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(10), default="success", comment="success/fail")
    vector: Mapped[str] = mapped_column(Text, comment="JSON list[float] ?????")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProjectPlaybook(Base):
    """??? Playbook ????"""

    __tablename__ = "project_playbook"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), unique=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=0)
    last_compressed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_appended_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True, index=True,
        comment="????????? episode_id?= task_id??????????? project_playbook_episode",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProjectPlaybookEpisode(Base):
    """Normalized playbook entry?"""

    __tablename__ = "project_playbook_episode"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "episode_id",
            name="uq_project_playbook_episode_project_episode",
        ),
        CheckConstraint(
            "outcome IN ('success', 'failure')",
            name="ck_project_playbook_episode_outcome",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=False,
    )
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), index=True, nullable=True,
        comment="episode?= task?id?? (project, episode) ???????????nullable = ??? / legacy ??",
    )
    task_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="dev",
        comment="entry ??? task_type?dev / bug / qa / design / legacy?",
    )
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default="success",
        comment="success / failure??????? '?? pattern' / '?? pattern' ??",
    )
    summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="???????????? markdown ??????",
    )
    weight: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0,
        comment="Learning Router ????????? 1.0????? judge / outcome ????",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, index=True,
        comment="????????????????",
    )


class Learning(Base):
    """????????????Configurable Agent Behavior & Learning??

    ?????????????
    - accepted_review_feedback: Owner ?????????
    - review_judgment_reversal: Reviewer ?????
    - qa_defect: QA ???? defect
    - execution_failure: ????????
    - project_convention: ?????????
    """

    __tablename__ = "learnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    work_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    lesson: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_review_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
