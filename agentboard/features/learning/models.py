"""学习域数据模型（Epic 140 切片 1）。

task_outcome：每个完成（done/blocked/withdrawn）任务的**结构化能力评分沉淀**。
- score: 0~1 复合分（本期 = L1/L2 过程指标公式；L3 LLM-judge 接入后加权）
- judge_json: 各维度明细（L1/L2 过程指标；L3 待 LLM judge 调度补齐，字段预留）
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
