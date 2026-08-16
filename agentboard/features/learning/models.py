"""学习域数据模型（Epic 140）。

切片 1 task_outcome：每个完成（done/blocked/withdrawn）任务的**结构化能力评分沉淀**。
- score: 0~1 复合分（本期 = L1/L2 过程指标公式；L3 LLM-judge 接入后加权）
- judge_json: 各维度明细（L1/L2 过程指标；L3 待 LLM judge 调度补齐，字段预留）

切片 3 Episode RAG + Playbook（Worker 持续学习，Story 268）：
- episode_embedding：每个完成任务的 run trace 向量化（零依赖 hash 向量 + numpy 可选加速），
  供新 task recall 相似经验注入 prompt。
- project_playbook：每个 project 一份结构化 markdown（已完成模式/失败教训），自动追加 + 摘要压缩。
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.models import Base, utc_now


class TaskOutcome(Base):
    __tablename__ = "task_outcome"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="ck_task_outcome_score"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 一个 task 唯一一条 outcome（重复计算幂等 upsert）
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(10), default="dev")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    # 明细 JSON：{"pass_first_try":1.0,"review_rounds":0,"attempts":1,
    #            "duration_s":3600,"blocked_count":0,"withdrawn":false,
    #            "judge_pending":true}
    judge_json: Mapped[str] = mapped_column(Text, default="{}")
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class EpisodeEmbedding(Base):
    """任务 run trace 的向量化快照（Story 268 切片 3）。

    - 每个 task 唯一一条 episode（episode_id == task_id，幂等 upsert）；
    - vector 存 JSON 文本（list[float]），兼容双后端（MariaDB TEXT / SQLite TEXT）；
    - summary 为人类可读的 episode 摘要（prompt 注入用）；
    - outcome 标记成功/失败（success / fail），recall 时成功 top-5 + 失败 top-3 分组注入。
    """

    __tablename__ = "episode_embedding"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"), unique=True, index=True, comment="任务 id（一任务一条 episode）"
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(10), default="dev")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(10), default="success", comment="success/fail")
    vector: Mapped[str] = mapped_column(Text, comment="JSON list[float] 归一化向量")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProjectPlaybook(Base):
    """项目级 Playbook（Story 268 切片 3）：结构化 markdown，按 project 唯一。"""

    __tablename__ = "project_playbook"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), unique=True, index=True
    )
    content_md: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_compressed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
