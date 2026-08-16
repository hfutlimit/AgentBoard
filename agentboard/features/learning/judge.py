"""L3 LLM-as-judge 调度（Epic 140 切片 2）。

分层设计（Story 267 §关键设计）：
- ``build_judge_input``：从 task + 评论 + 状态历史 + L1/L2 指标构建 judge 输入（纯函数）。
- ``deterministic_judge``：**无 LLM 配置时的确定性降级**（启发式，从 L1/L2 + 输入信号推导 L3，
  保证 dashboard 冷启动即有完整 L3 明细，绝不依赖外部服务）。
- ``call_llm_judge``：OpenAI 兼容 chat/completions（标准库 urllib，零新增依赖），
  超时 / 网络失败 / 非法 JSON 一律降级 deterministic，绝不抛到主流程。
- ``judge_task``：主入口——outcome → input → judge → schema 校验 → 回填 + score 重算。
- ``schedule_judge``：set_status 终态后后台 daemon 线程异步触发（独立 Session，失败吞异常）。

降级优先级：LLM 可用（AGENTBOARD_JUDGE_API_URL）且未超 daily quota → llm；
否则 deterministic。judge_json.judge_provider 记录实际 provider，供 UI 标注置信度。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, time as dt_time

from sqlalchemy import select

from . import judge_prompt
from .models import TaskOutcome

logger = logging.getLogger(__name__)

# LLM 调用超时（秒）——judge 是增强数据，慢/失败都不应阻塞任何主流程
_LLM_TIMEOUT_S = 20.0

# 降级评分用的启发式常量
_TEST_KEYWORDS = ("test", "测试", "pytest", "e2e", "回归", "用例", "spec")


def is_judge_llm_enabled() -> bool:
    """是否配置了 judge LLM（配置 URL 即启用）。"""
    return bool(os.environ.get("AGENTBOARD_JUDGE_API_URL", "").strip())


def daily_llm_quota() -> int:
    raw = os.environ.get("AGENTBOARD_JUDGE_DAILY_QUOTA", "200")
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 200


def _llm_daily_used(s) -> int:
    """今日已走 LLM judge 的 outcome 数（按 updated_at + judge_provider=llm 统计）。"""
    from ...core.common.models import utc_now

    today = utc_now().date()
    start = datetime.combine(today, dt_time.min)
    rows = s.execute(
        select(TaskOutcome.updated_at, TaskOutcome.judge_json)
        .where(TaskOutcome.updated_at >= start)
    ).all()
    used = 0
    for _updated, jj in rows:
        try:
            data = json.loads(jj or "{}")
        except (TypeError, ValueError):
            continue
        if data.get("judge_provider") == "llm":
            used += 1
    return used


def build_judge_input(s, task, metrics: dict) -> dict:
    """从 task 全上下文构建 judge 输入（用于 LLM prompt 与 deterministic 降级）。"""
    from ..work_items.models import Comment, TaskStatusHistory

    transitions_rows = (
        s.execute(
            select(TaskStatusHistory.from_status, TaskStatusHistory.to_status)
            .where(TaskStatusHistory.task_id == task.id)
            .order_by(TaskStatusHistory.id)
        ).all()
    )
    transitions = [f"{f} -> {t}" for f, t in transitions_rows]

    comments_rows = (
        s.execute(
            select(Comment.author, Comment.content, Comment.created_at)
            .where(Comment.task_id == task.id)
            .order_by(Comment.id)
        ).all()
    )
    comments = [
        {"author": a, "content": c[:2000], "created_at": ts.isoformat() if ts else None}
        for a, c, ts in comments_rows
    ]

    return {
        "title": task.title or "",
        "task_type": task.type or "dev",
        "status": task.status or "",
        "status_reason": task.status_reason,
        "priority": task.priority or "medium",
        "labels": task.labels or "[]",
        "spec": (task.spec or "").strip() or (task.description or "").strip(),
        "transitions": transitions,
        "metrics": metrics,
        "comments": comments,
    }


def _clamp01(x: float) -> float:
    return round(min(max(float(x), 0.0), 1.0), 4)


def deterministic_judge(inp: dict, metrics: dict) -> dict:
    """无 LLM 时的确定性降级评分（启发式，全部可从输入证据推导，零幻觉）。"""
    spec_text = (inp.get("spec") or "").lower()
    comments = inp.get("comments") or []
    comment_text = " ".join((c.get("content") or "") for c in comments).lower()
    reason = (inp.get("status_reason") or "").lower()

    # spec_coverage：spec/description 非空即有基础分；有实现细节/评论佐证则更高
    if spec_text:
        spec_coverage = 0.75 + (0.15 if len(spec_text) >= 200 else 0.0) + (0.05 if comment_text else 0.0)
    else:
        spec_coverage = 0.4
    spec_coverage = _clamp01(spec_coverage)

    # code_quality：评审打回（reject）次数越多越低
    rejects = int(metrics.get("rejects") or 0)
    code_quality = _clamp01(1.0 - 0.25 * rejects - (0.1 if "todo" in comment_text else 0.0))

    # test_coverage：spec/评论提及测试证据则高，否则保守中性 0.55
    combined = f"{spec_text} {comment_text}"
    test_coverage = 0.85 if any(k in combined for k in _TEST_KEYWORDS) else 0.55

    # spec_drift：completed 且无 reject → 高；withdrawn/blocked 视理由充分度
    status = (inp.get("status") or "").lower()
    if status == "done" and metrics.get("pass_first_try") == 1.0:
        spec_drift = 0.9
    elif status == "done":
        spec_drift = 0.7
    elif status == "blocked" and reason:
        spec_drift = 0.6
    elif reason == "withdrawn":
        spec_drift = 0.5
    else:
        spec_drift = 0.5

    # reason_quality：status_reason 存在且非占位即合理（与 L1 同源，LLM 模式可更严格）
    reason_quality = 1.0 if (metrics.get("reason_quality") or 0) == 1.0 else 0.0

    judge_quality = _clamp01(
        sum([spec_coverage, code_quality, test_coverage, spec_drift, reason_quality]) / 5.0
    )
    return {
        "provider": "deterministic",
        "spec_coverage": spec_coverage,
        "code_quality": code_quality,
        "test_coverage": test_coverage,
        "spec_drift": spec_drift,
        "reason_quality": reason_quality,
        "judge_quality": judge_quality,
        "rationale": (
            f"确定性降级评分（未配置 AGENTBOARD_JUDGE_API_URL）："
            f"rejects={rejects}, spec_len={len(spec_text)}, "
            f"test_evidence={'有' if test_coverage >= 0.8 else '无'}"
        ),
    }


def _parse_llm_json(content: str) -> dict | None:
    """解析 LLM 返回的 JSON（容忍 markdown 代码块包裹）。"""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        # 尝试提取第一个 { ... } 片段
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    return data


def _validate_judge_result(data: dict) -> dict | None:
    """schema 校验 + 归一化：5 个 L3 子分（0~1）+ judge_quality + rationale。"""
    try:
        keys = [k for k in judge_prompt.JUDGE_KEYS if k in data]
        if not keys:
            return None
        scores = {k: _clamp01(data[k]) for k in keys}
        # 缺失维度用其余维度均值补（宽容）
        if len(scores) < 5:
            avg = sum(scores.values()) / len(scores)
            for k in judge_prompt.JUDGE_KEYS:
                scores.setdefault(k, round(avg, 4))
        jq = data.get("judge_quality")
        judge_quality = _clamp01(jq) if isinstance(jq, (int, float)) else round(sum(scores.values()) / 5.0, 4)
        rationale = str(data.get("rationale") or "")[:500]
        return {
            "provider": "llm",
            **scores,
            "judge_quality": judge_quality,
            "rationale": rationale,
        }
    except (TypeError, ValueError):
        return None


def call_llm_judge(inp: dict) -> dict | None:
    """OpenAI 兼容 chat/completions 调用。失败返回 None（调用方降级）。"""
    url = os.environ.get("AGENTBOARD_JUDGE_API_URL", "").strip()
    if not url:
        return None
    api_key = os.environ.get("AGENTBOARD_JUDGE_API_KEY", "").strip()
    model = os.environ.get("AGENTBOARD_JUDGE_MODEL", "gpt-4o-mini")

    # 若 URL 是 base（如 https://api.openai.com/v1），自动拼 /chat/completions
    if url.endswith("/"):
        url = url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"

    from .judge_prompt import build_user_prompt

    user_prompt = build_user_prompt(
        title=inp["title"],
        task_type=inp["task_type"],
        status=inp["status"],
        status_reason=inp.get("status_reason"),
        priority=inp["priority"],
        labels=inp["labels"],
        spec=inp["spec"],
        transitions="\n".join(inp["transitions"]) if inp["transitions"] else "(无)",
        metrics=json.dumps(inp["metrics"], ensure_ascii=False),
        comments=json.dumps(inp["comments"], ensure_ascii=False)[:4000],
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": judge_prompt.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("judge llm call failed: %s", exc)
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return _validate_judge_result(_parse_llm_json(content))


def judge_task(s, task_id: int) -> dict | None:
    """对已到终态且有 outcome 的 task 执行 judge 并回填（同步；调用方负责 commit）。

    返回 judge 摘要 dict；task 不存在 / 非终态 / 无 outcome 返回 None。
    任何异常都向内吞掉并返回 None（judge 属增强数据，绝不阻断主流程）。
    """
    from ..work_items.models import Task
    from . import service as learning_service
    from .models import TaskOutcome

    try:
        task = s.get(Task, task_id)
        if task is None:
            return None
        if task.status not in (learning_service._TERMINAL_STATUSES):
            return None
        outcome = s.execute(
            select(TaskOutcome).where(TaskOutcome.task_id == task.id)
        ).scalar_one_or_none()
        if outcome is None:
            return None

        metrics = learning_service.compute_process_metrics(s, task)
        inp = build_judge_input(s, task, metrics)

        if is_judge_llm_enabled() and _llm_daily_used(s) < daily_llm_quota():
            result = call_llm_judge(inp) or deterministic_judge(inp, metrics)
        else:
            result = deterministic_judge(inp, metrics)

        learning_service.apply_judge(s, outcome, metrics, result)
        return {
            "task_id": task.id,
            "provider": result["provider"],
            "judge_quality": result["judge_quality"],
            "score": outcome.score,
        }
    except Exception:  # noqa: BLE001 —— judge 是增强数据，任何失败都不外泄
        logger.exception("judge task %s failed", task_id)
        return None


def schedule_judge(task_id: int) -> None:
    """set_status 终态后后台 daemon 线程异步 judge（独立 Session，失败吞异常）。"""

    def _run() -> None:
        try:
            from ...core.infrastructure.database import SessionLocal
            with SessionLocal() as s:
                judge_task(s, task_id)
                s.commit()
        except Exception:  # noqa: BLE001
            logger.exception("async judge task %s failed", task_id)

    threading.Thread(target=_run, daemon=True, name=f"judge-{task_id}").start()
