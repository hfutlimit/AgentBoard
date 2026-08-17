"""学习域服务（Epic 140 切片 1）：过程指标计算 + outcome 落库 + leaderboard 聚合。

- compute_process_metrics: 从 task_status_history 计算 L1 任务结果 / L2 过程质量。
- record_outcome: task 到达终态（done/blocked/withdrawn）时幂等落 task_outcome。
- apply_judge: L3 judge 结果回填（judge_json + score 重算），切片 2 调用。
- agent_leaderboard: 按 (agent_id, project_id, task_type) 多维聚合评分矩阵。

L3（LLM-as-judge 产出质量）由切片 2 judge.py 计算后经 apply_judge 回填；
未接入前 judge_quality 取中性占位（低置信，UI 标注）。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select

from ...core.common.models import utc_now
from ...core.common.enums import Status, StatusReason
from ...core.exceptions import InvalidValue
from .models import TaskOutcome

# 复合评分权重（Story 267 公式，L3 未接入时 judge_quality 取 0.75 中性占位）
W_PASS_FIRST = 0.4
W_JUDGE = 0.3
W_CYCLE = 0.2
W_REASON = 0.1
NEUTRAL_JUDGE_QUALITY = 0.75  # 切片 2 LLM-judge 接入前的占位（低置信，UI 标注）

# 任务终态（done / blocked）：学习 outcome 落库 + 异步 judge 调度的触发条件。
# 公开常量（无下划线）供跨模块引用，避免 judge.py 访问下划线私有约定。
TERMINAL_STATUSES: frozenset[str] = frozenset({Status.DONE, Status.BLOCKED})

# 内部别名，保留老引用兼容（Phase 4 拆分前 judge.py 等模块已用 _TERMINAL_STATUSES）
_TERMINAL_STATUSES = TERMINAL_STATUSES


def compute_process_metrics(s, task) -> dict:
    """从 task_status_history 计算 L1 任务结果 + L2 过程质量。

    全部为纯统计（无 LLM），可在事务内安全调用。
    """
    from ..work_items.models import TaskStatusHistory

    rows = (
        s.execute(
            select(TaskStatusHistory.from_status, TaskStatusHistory.to_status)
            .where(TaskStatusHistory.task_id == task.id)
            .order_by(TaskStatusHistory.id)
        ).all()
    )
    transitions = [(r[0], r[1]) for r in rows]

    review_rounds = sum(1 for f, t in transitions if t == Status.IN_REVIEW)
    rejects = sum(1 for f, t in transitions if f == Status.IN_REVIEW and t == Status.IN_PROGRESS)
    blocked_count = sum(1 for f, t in transitions if t == Status.BLOCKED)
    attempts = max(len(transitions), 1)
    pass_first_try = 1.0 if rejects == 0 else 0.0

    # 时长：created_at → updated_at（未终态时为空）
    duration_s: int | None = None
    if task.created_at:
        end = task.updated_at or utc_now()
        try:
            duration_s = max(int((end - task.created_at).total_seconds()), 0)
        except (TypeError, ValueError):
            duration_s = None

    withdrawn = bool(task.status == Status.DONE and task.status_reason == StatusReason.WITHDRAWN)

    # 循环效率：评审往返越少越高（1 轮往返 = 1.0，每多 1 轮 -0.25，下限 0.2）
    cycle_efficiency = max(0.2, 1.0 - 0.25 * max(rejects - 1, 0))
    # 原因质量：终态必填 status_reason 且非占位即合理
    reason_quality = 1.0 if task.status_reason else 0.0

    score = (
        W_PASS_FIRST * pass_first_try
        + W_JUDGE * NEUTRAL_JUDGE_QUALITY
        + W_CYCLE * cycle_efficiency
        + W_REASON * reason_quality
    )
    score = round(min(max(score, 0.0), 1.0), 4)

    return {
        "pass_first_try": pass_first_try,
        "review_rounds": review_rounds,
        "rejects": rejects,
        "blocked_count": blocked_count,
        "attempts": attempts,
        "duration_s": duration_s,
        "withdrawn": withdrawn,
        "cycle_efficiency": round(cycle_efficiency, 4),
        "reason_quality": reason_quality,
        "score": score,
        "judge_pending": True,  # L3 待切片 2 LLM judge 回填
    }


def record_outcome(s, task) -> TaskOutcome | None:
    """task 到达终态时幂等落 task_outcome（task_id 唯一，重复计算为更新）。"""
    if task.status not in TERMINAL_STATUSES:
        return None
    metrics = compute_process_metrics(s, task)
    existing = s.execute(
        select(TaskOutcome).where(TaskOutcome.task_id == task.id)
    ).scalar_one_or_none()
    if existing is None:
        existing = TaskOutcome(
            task_id=task.id,
            project_id=task.project_id,
            agent_id=task.assignee_id,
            task_type=task.type or "dev",
            score=metrics["score"],
            judge_json=json.dumps(metrics, ensure_ascii=False),
            duration_s=metrics["duration_s"],
            attempts=metrics["attempts"],
        )
        s.add(existing)
    else:
        existing.score = metrics["score"]
        existing.judge_json = json.dumps(metrics, ensure_ascii=False)
        existing.duration_s = metrics["duration_s"]
        existing.attempts = metrics["attempts"]
        existing.updated_at = utc_now()
    return existing


def apply_judge(s, outcome: TaskOutcome, metrics: dict, judge_result: dict) -> None:
    """L3 judge 结果回填（幂等）：合并进 judge_json + 按复合公式重算 score。

    judge_result 结构（judge.py 产出）：
        provider / spec_coverage / code_quality / test_coverage /
        spec_drift / reason_quality / judge_quality / rationale
    """
    data = dict(metrics)
    data.update({
        "judge_pending": False,
        "judge_provider": judge_result.get("provider", "deterministic"),
        "judge_quality": judge_result.get("judge_quality", NEUTRAL_JUDGE_QUALITY),
        "rationale": judge_result.get("rationale", ""),
    })
    for k in ("spec_coverage", "code_quality", "test_coverage", "spec_drift", "reason_quality"):
        if k in judge_result:
            data[k] = judge_result[k]

    judge_quality = float(data["judge_quality"])
    score = (
        W_PASS_FIRST * float(data.get("pass_first_try", 0.0))
        + W_JUDGE * judge_quality
        + W_CYCLE * float(data.get("cycle_efficiency", 0.0))
        + W_REASON * float(data.get("reason_quality", 0.0))
    )
    score = round(min(max(score, 0.0), 1.0), 4)

    outcome.judge_json = json.dumps(data, ensure_ascii=False)
    outcome.score = score
    outcome.updated_at = utc_now()


def agent_leaderboard(
    s, *, project_id: int | None = None, task_type: str | None = None, limit: int = 50,
) -> list[dict]:
    """多维聚合：(agent_id, project_id, task_type) → 样本数 + 平均分。

    agent_id 为空（未指派）归入 None 桶，仍参与聚合（保证终态任务全覆盖）。
    """
    if limit < 1 or limit > 200:
        raise InvalidValue("limit must be between 1 and 200")
    stmt = (
        select(
            TaskOutcome.agent_id,
            TaskOutcome.project_id,
            TaskOutcome.task_type,
            func.count(TaskOutcome.id).label("n"),
            func.avg(TaskOutcome.score).label("avg_score"),
        )
        .group_by(TaskOutcome.agent_id, TaskOutcome.project_id, TaskOutcome.task_type)
        .order_by(func.avg(TaskOutcome.score).desc(), func.count(TaskOutcome.id).desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(TaskOutcome.project_id == project_id)
    if task_type:
        stmt = stmt.where(TaskOutcome.task_type == task_type)

    out = []
    for row in s.execute(stmt).all():
        out.append({
            "agent_id": row.agent_id,
            "project_id": row.project_id,
            "task_type": row.task_type,
            "tasks": row.n,
            "avg_score": round(float(row.avg_score or 0.0), 4),
        })
    return out


def list_outcomes(
    s, *, project_id: int | None = None, task_id: int | None = None, limit: int = 50,
) -> list[dict]:
    """outcome 明细（dashboard 表格 / 调试用）。"""
    stmt = select(TaskOutcome).order_by(TaskOutcome.updated_at.desc()).limit(limit)
    if project_id is not None:
        stmt = stmt.where(TaskOutcome.project_id == project_id)
    if task_id is not None:
        stmt = stmt.where(TaskOutcome.task_id == task_id)
    out = []
    for o in s.execute(stmt).scalars().all():
        out.append({
            "id": o.id,
            "task_id": o.task_id,
            "project_id": o.project_id,
            "agent_id": o.agent_id,
            "task_type": o.task_type,
            "score": o.score,
            "judge_json": json.loads(o.judge_json or "{}"),
            "duration_s": o.duration_s,
            "attempts": o.attempts,
            "updated_at": o.updated_at.isoformat() if o.updated_at else None,
        })
    return out
