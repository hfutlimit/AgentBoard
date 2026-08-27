"""Scheduling service:Schedule / Run / Agent / Review。

Phase 4 第五段:从 service.py 拆分。本文件仅作 facade 装载新模块;老 import
路径由 service.py 末尾 ``from .features.X.service import *`` 重绑保持兼容。

本文件不实现业务逻辑,只是把 service.py 里同主题的函数搬家过来 + 加必要的
import,行为完全一致。
"""
from __future__ import annotations

import json
import logging
import os
import re as _re
from datetime import datetime, timedelta

from sqlalchemy import or_, and_, func, update
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容

log = logging.getLogger("agentboard.features.scheduling.service")

from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
    Duplicate,
    Forbidden,
    IllegalTransition,
    InvalidValue,
    NotFound,
)

from ...core.service_helpers import (
    _commit, _invalidate_project_stats_cache, _paginate, _required,
    _parse_json_list, validate_cli_command,
)

from ...core.common.enums import (
    ALL_PRIORITIES,
    ALL_RUN_STATUSES,
    ALL_SCHEDULE_TYPES,
    ALL_STATUSES,
    ALL_TYPES,
    Priority,
    SprintStatus,
    Status,
    StatusReason,
)
from ...core.common.models import utc_now  # noqa: F401

from ..identity.models import (
    User,
)

from ..projects.models import (
    Agent,
    AgentInstance,
    Epic,
    ProjectMember,
    ReviewVote,
    Sprint,
    Story,
    Worker,
)
from ..projects.service import _record_story_status_history  # noqa: F401  (跨域 helper)
from ..projects.service import (  # noqa: F401  (跨域 helper)
    expire_stale_agent_heartbeats,
    get_agent_by_agent_id,
)
from ..work_items.service import (  # noqa: E402 — 跨域调用（评审/评论/状态历史走任务域）
    _record_status_history,
    _record_learning_outcome,
    create_comment,
    finalize_task_assignment,
    set_status,
    try_assign_task,
)
from ..work_items.models import Comment  # noqa: E402 — 评审意见评论实体
from ..projects.models import STORY_REVIEW_STATUSES  # noqa: E402 — Story 级评审态（恒空占位）

from .models import (
    DEFAULT_REVIEW_QUORUM,
    DEFAULT_REVIEW_TIMEOUT_MINUTES,
    DEFAULT_TIMEOUT_SCAN_BATCH,
    MAX_REVIEW_ROUNDS,
    REVIEW_MODE_MAJORITY,
    REVIEW_MODE_SINGLE,
)


# 评审超时(30 分钟,任务超过这个时间还没人评审就重新指派)
DEFAULT_REVIEW_TIMEOUT_MINUTES = 30
DEFAULT_TIMEOUT_SCAN_BATCH = 20
MAX_REVIEW_ROUNDS = 5
DEFAULT_REVIEW_QUORUM = 3


# Agent 在 projects.models
from ..projects.models import Project
from ..work_items.models import Task
from ..work_items.service import set_status  # noqa: E402 — 跨域调用（提交评审走任务状态机）
from .models import AgentRun, AgentSchedule
from .matching import normalize_capabilities, rank_agents_for_task


# ---- 调度相关常量（从顶层 service.py 迁移，Phase 9 收口） ----

_CRON_PATTERN = _re.compile(
    # 支持 */n 步长语法（如 */1 每分钟，*/5 每5分钟）
    r"^(\*(?:/\d+)?|[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?(?:,[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?)*)\s+"
    r"(\*(?:/\d+)?|1?\d|2[0-3])(?:-[1-2]?\d(?:/\d+)?)?(?:,(?:1?\d|#[0-3]))*\s+"
    r"(\*(?:/\d+)?|[1-2]?\d|3[01])(?:-[1-3]?\d(?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|1?\d|1[0-2])(?:-1[0-2](?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|[0-7])(?:-[0-7](?:/\d+)?)?(?:,[0-7](?:-[0-7](?:/\d+)?)?)*$"
)

#: 可绑定到 AgentSchedule.agent 的合法 Agent 名（与 executor.KNOWN_AGENTS 对应）
SCHEDULE_AGENTS = ("codex", "claude", "workbuddy", "qoder", "minimax")

#: 任务优先级权重（值越大优先级越高；用于 pick_eligible_task 排序与门槛）
PRIORITY_RANK = {
    Priority.HIGHEST: 5, Priority.HIGH: 4, Priority.MEDIUM: 3,
    Priority.LOW: 2, Priority.LOWEST: 1,
}

#: 执行器可自动领取的任务状态（Story 265 后仅 todo）
ELIGIBLE_TASK_STATUSES = (Status.TODO,)


def create_run(s: Session, *, schedule_id: int, task_id: int | None = None,
               idempotency_key: str | None = None) -> AgentRun:
    schedule = s.get(AgentSchedule, schedule_id)
    if not schedule:
        raise NotFound(f"schedule {schedule_id} not found")
    if idempotency_key:
        existing = s.query(AgentRun).filter(AgentRun.idempotency_key == idempotency_key).first()
        if existing:
            raise Duplicate(f"run with idempotency_key '{idempotency_key}' already exists")
    agent_id = schedule.agent or os.environ.get("AGENTBOARD_DEFAULT_AGENT", "codex")
    agent_config = get_agent_by_agent_id(s, agent_id)
    if agent_config is None:
        agent_config = Agent(
            agent_id=agent_id,
            name=agent_id.replace("-", " ").title(),
            roles='["developer"]',
            capabilities="[]",
            cli_command="",
            model="",
            auth_key="",
            enabled=True,
            online=False,
        )
        s.add(agent_config)
        s.flush()
    assignment = None
    if task_id is not None:
        _task, assignment = try_assign_task(
            s,
            task_id,
            user_id=agent_config.user_id,
            agent_registry_id=agent_config.id,
            source="schedule",
            commit=False,
        )
    run = AgentRun(
        schedule_id=schedule_id, task_id=task_id,
        agent_registry_id=agent_config.id,
        assignment_id=assignment.id if assignment else None,
        agent=agent_id, model=agent_config.model if agent_config else None,
        idempotency_key=idempotency_key,
    )
    s.add(run)
    _commit(
        s,
        duplicate=(
            f"run with idempotency_key '{idempotency_key}' already exists"
            if idempotency_key else None
        ),
    )
    s.refresh(run)
    return run



from .models import RunEvent

def create_run_event(
    s: Session,
    run_id: int,
    event_type: str,
    payload: dict,
    *,
    actor_user_id: int | None = None,
    api_key_id: int | None = None,
    agent_registry_id: int | None = None,
    worker_id: str | None = None,
    actor_username_snapshot: str | None = None,
    api_key_prefix_snapshot: str | None = None,
    agent_ref_snapshot: str | None = None,
) -> RunEvent:
    run = s.get(AgentRun, run_id)
    if not run:
        raise NotFound(f"run {run_id} not found")
    event = RunEvent(
        run_id=run_id,
        event_type=event_type,
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        actor_user_id=actor_user_id,
        api_key_id=api_key_id,
        agent_registry_id=agent_registry_id,
        worker_id=worker_id,
        actor_username_snapshot=actor_username_snapshot,
        api_key_prefix_snapshot=api_key_prefix_snapshot,
        agent_ref_snapshot=agent_ref_snapshot,
    )
    s.add(event)
    _commit(s)
    s.refresh(event)
    return event

def list_run_events(
    s: Session,
    run_id: int,
    limit: int | None = 200,
    offset: int = 0,
    after_id: int = 0,
    before_id: int | None = None,
    order: str = "asc",
):
    """List run events for one run.

    ``order='asc'`` (default) returns the oldest events first — the natural
    shape for replay and pagination. ``order='desc'`` returns the newest
    first, which is what the ``before_id`` window wants for efficient
    scroll queries. Callers are explicit about which they need so the
    router no longer has to ``rows.reverse()`` after the fact.
    """
    q = s.query(RunEvent).filter(RunEvent.run_id == run_id)
    if before_id is not None:
        q = q.filter(RunEvent.id < before_id)
    else:
        q = q.filter(RunEvent.id > after_id)
    if order == "desc":
        q = q.order_by(RunEvent.id.desc())
    else:
        q = q.order_by(RunEvent.id.asc())
    return _paginate(q, limit, offset).all()

def claim_lease(s: Session, run_id: int, worker_id: str, ttl_seconds: int = 60) -> bool:
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    r = s.execute(
        update(AgentRun).where(
            and_(
                AgentRun.id == run_id,
                AgentRun.status.in_(("pending", "running")),
                or_(
                    AgentRun.lease_worker_id == None,
                    AgentRun.lease_expires_at < now,
                    AgentRun.lease_worker_id == worker_id
                )
            )
        ).values(
            lease_worker_id=worker_id,
            lease_expires_at=expires_at
        )
    )
    _commit(s)
    return r.rowcount > 0

def release_lease(s: Session, run_id: int, worker_id: str) -> bool:
    r = s.execute(
        update(AgentRun).where(
            and_(AgentRun.id == run_id, AgentRun.lease_worker_id == worker_id)
        ).values(
            lease_worker_id=None,
            lease_expires_at=None
        )
    )
    _commit(s)
    return r.rowcount > 0


def list_runs(s: Session, schedule_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(AgentRun).filter(AgentRun.schedule_id == schedule_id).order_by(AgentRun.id.desc())
    return _paginate(q, limit, offset).all()


def unclaim_story(s: Session, id: int, *, changed_by: int | None = None,
                  reason: str = "") -> Story:
    """Worker 认领交接/失败回退（Ticket 全流程）：CAS todo → confirmed。

    - agent 本轮未完成全部 task（部分推进）→ 回退 confirmed，下轮/其它实例再领；
    - agent 失败重试 → 回退 confirmed 重新入池（连续失败达上限转 blocked 不回退）。
    todo → confirmed 不在常规迁移表（todo 出边仅 in_progress/backlog/blocked），
    本入口为编排专用 CAS（同 complete_story 模式），blocked 不操作。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    if st.status == "blocked":
        raise IllegalTransition(f"story {id} 处于 blocked，禁止回退（需人工仲裁）")
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == "todo")
        .values(status="confirmed", claimed_by="", claimed_at=None)
    )
    if r.rowcount != 1:
        s.rollback()
        cur = s.get(Story, id)
        if cur is not None and cur.status == "confirmed":
            raise IllegalTransition(f"story {id} 已是 confirmed")
        raise IllegalTransition(f"story {id} 当前状态 {cur.status if cur else '?'}，不可回退")
    _record_story_status_history(s, id, "todo", "confirmed", changed_by=changed_by,
                                 reason=reason or "Worker 交接/失败回退")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st


def update_run(s: Session, id: int, **fields) -> AgentRun | None:
    run = s.get(AgentRun, id)
    if not run:
        return None
    for k, v in fields.items():
        if k == "status" and v is not None:
            if v not in ALL_RUN_STATUSES:
                raise InvalidValue(f"invalid run status '{v}'")
            current = str(run.status)
            if v != current and v not in RUN_TRANSITIONS.get(current, set()):
                raise IllegalTransition(f"run status {current} -> {v} illegal")
            run.status = v
        elif k == "output" and v is not None:
            run.output = v
        elif k == "error_message" and v is not None:
            run.error_message = v
        elif k == "summary" and v is not None:
            run.summary = v
        elif k == "log_ref" and v is not None:
            run.log_ref = v
        elif k == "started_at" and v is not None:
            run.started_at = v
        elif k == "finished_at" and v is not None:
            run.finished_at = v
        elif k == "task_id" and v is not None:
            run.task_id = v
    _commit(s); s.refresh(run); return run


# AgentRun 状态机合法迁移表（Story 104）
# pending → running（执行器认领）；running → success/failed/cancelled（Agent 回写或执行器检测）
# 终态 success/failed/cancelled 不可再迁移。
RUN_TRANSITIONS = {
    "pending": {"running", "success", "failed", "cancelled"},
    "running": {"success", "failed", "cancelled"},
    "success": set(),
    "failed": set(),
    "cancelled": set(),
}


def delete_schedule(s: Session, id: int) -> bool:
    sch = s.get(AgentSchedule, id)
    if not sch:
        return False
    s.delete(sch); _commit(s); return True


def register_agent(s: Session, *, agent_id: str, name: str, roles: str = "[]",
                   capabilities="[]", cli_command: str = "",
                   model: str = "", auth_key: str = "", user_id: int | None = None) -> Agent:
    """注册/更新 Agent（幂等：agent_id 已存在则更新字段）。

    agent_id 为外部 Agent 自报唯一标识；roles/capabilities 为 JSON 数组串。
    cli_command 支持 ``{model}`` 占位符（同一 CLI 多 agent 各自注入模型）。
    user_id 绑定服务账号用户（经 ProjectMember 授权参与项目协作）。
    """
    agent_id = _required(agent_id, "agent_id", 64)
    name = _required(name, "name", 100)
    roles_list = _parse_json_list(roles, "roles")
    caps_list = normalize_capabilities(capabilities)
    # B-A2: cli_command 安全校验（防 shell 注入，与 probe dry-run 配合）
    validate_cli_command(cli_command)
    if user_id is not None and not s.get(User, user_id):
        raise NotFound(f"user {user_id} not found")
    existing = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if existing:
        existing.name = name
        existing.roles = json.dumps(roles_list, ensure_ascii=False)
        existing.capabilities = json.dumps(caps_list, ensure_ascii=False)
        existing.cli_command = (cli_command or "")[:500]
        existing.model = (model or "")[:100]
        existing.auth_key = (auth_key or "")[:100]
        if user_id is not None:
            existing.user_id = user_id
        _commit(s); s.refresh(existing); return existing
    agent = Agent(
        agent_id=agent_id,
        name=name,
        roles=json.dumps(roles_list, ensure_ascii=False),
        capabilities=json.dumps(caps_list, ensure_ascii=False),
        cli_command=(cli_command or "")[:500],
        model=(model or "")[:100],
        auth_key=(auth_key or "")[:100],
        user_id=user_id,
        online=False,
    )
    s.add(agent)
    try:
        _commit(s); s.refresh(agent); return agent
    except Duplicate:
        # 并发注册：回查返回既有记录
        s.rollback()
        existing = s.query(Agent).filter(Agent.agent_id == agent_id).first()
        if existing:
            return existing
        raise


def update_agent(s: Session, agent_id: str, **fields) -> Agent | None:
    """Update an Agent configuration while preserving profile normalization."""
    agent = get_agent_by_agent_id(s, agent_id)
    if not agent:
        return None
    if "name" in fields and fields["name"] is not None:
        agent.name = _required(fields["name"], "name", 100)
    if "roles" in fields and fields["roles"] is not None:
        agent.roles = json.dumps(
            _parse_json_list(fields["roles"], "roles"), ensure_ascii=False
        )
    if "capabilities" in fields and fields["capabilities"] is not None:
        agent.capabilities = json.dumps(
            normalize_capabilities(fields["capabilities"]), ensure_ascii=False
        )
    if "cli_command" in fields and fields["cli_command"] is not None:
        validate_cli_command(fields["cli_command"])
        agent.cli_command = str(fields["cli_command"] or "")[:500]
    if "model" in fields and fields["model"] is not None:
        agent.model = str(fields["model"] or "")[:100]
    if "enabled" in fields and fields["enabled"] is not None:
        agent.enabled = bool(fields["enabled"])
    if "user_id" in fields:
        user_id = fields["user_id"]
        if user_id is not None and not s.get(User, user_id):
            raise NotFound(f"user {user_id} not found")
        agent.user_id = user_id
    _commit(s)
    s.refresh(agent)
    return agent


def scan_review_timeouts(s: Session, *, project_id: int | None = None,
                         timeout_minutes: int = DEFAULT_REVIEW_TIMEOUT_MINUTES,
                         max_per_run: int = DEFAULT_TIMEOUT_SCAN_BATCH,
                         now: datetime | None = None) -> dict:
    """评审超时自愈扫描（S3 M2 护栏）。

    超时定义：pending_review Story / in_review Task 且 reviewer 已指派且「最后活动」
    超时 —— Story 最后活动 = max(created_at, 最新评论时间)；Task 用 updated_at。
    处理：轮次达 MAX_REVIEW_ROUNDS → blocked（护栏终态）；否则 CAS 解绑旧 reviewer →
    重新随机指派（排除旧 reviewer，Task 版额外排除 assignee）；无候选 → 保持解绑
    由下轮轮询补派。解绑 CAS 带旧 reviewer_id 仲裁，多 worker 并发恰一赢家。
    """
    now = now or utc_now()
    timeout = timedelta(minutes=max(1, timeout_minutes))
    result = {"stories_reassigned": 0, "tasks_reassigned": 0,
              "blocked": 0, "no_candidate": 0,
              # S3 M3：majority 模式超时按现有票兜底结算计数（防死锁）
              "stories_settled": 0, "tasks_settled": 0,
              # 内部重派详情（(entity_id, new_reviewer_id)），供 API 层发布事件，响应中剔除
              "_stories_reassigned": [], "_tasks_reassigned": []}

    st_q = s.query(Story).filter(
        Story.status == "pending_review",
        Story.reviewer_id.isnot(None),
    )
    if project_id is not None:
        st_q = st_q.join(Epic, Story.epic_id == Epic.id).filter(
            Epic.project_id == project_id)
    overdue_stories = [
        st for st in st_q.order_by(Story.id.asc()).limit(max_per_run).all()
        if now - _story_last_activity(s, st) > timeout
    ]
    for st in overdue_stories:
        # S3 M3：majority 模式超时按现有票兜底结算（approve>reject → 通过；
        # reject>=approve → 驳回；平局保守驳回防死锁）。零票走既有重派逻辑。
        if get_review_mode() == REVIEW_MODE_MAJORITY:
            approve_n, reject_n = _review_vote_counts(s, "story", st.id)
            if approve_n + reject_n > 0:
                if approve_n > reject_n:
                    _settle_majority_approved(s, st, "story")
                    result["stories_settled"] += 1
                else:
                    settled = _settle_majority_rejected(s, st, "story")
                    result["stories_settled"] += 1
                    if settled.status == "blocked":
                        result["blocked"] += 1
                continue
        if (st.review_round or 0) >= MAX_REVIEW_ROUNDS:
            r = s.execute(update(Story).where(
                Story.id == st.id,
                Story.status == "pending_review",
            ).values(status="blocked"))
            if r.rowcount == 1:
                _commit(s)
                result["blocked"] += 1
            else:
                s.rollback()
            continue
        old = st.reviewer_id
        r = s.execute(update(Story).where(
            Story.id == st.id,
            Story.reviewer_id == old,
        ).values(reviewer_id=None))
        if r.rowcount != 1:
            s.rollback()
            continue  # 并发写者已抢先处理
        _commit(s)
        fresh = s.get(Story, st.id)
        if fresh is None:
            continue
        new_rev = _reassign_story_reviewer(s, fresh, exclude_user_id=old)
        if new_rev is not None:
            result["stories_reassigned"] += 1
            result["_stories_reassigned"].append((fresh.id, new_rev))
        else:
            result["no_candidate"] += 1

    t_q = s.query(Task).filter(
        Task.status == Status.IN_REVIEW,
        Task.reviewer_id.isnot(None),
    )
    if project_id is not None:
        t_q = t_q.filter(Task.project_id == project_id)
    overdue_tasks = [
        t for t in t_q.order_by(Task.id.asc()).limit(max_per_run).all()
        if now - t.updated_at > timeout
    ]
    for t in overdue_tasks:
        # S3 M3：majority 模式超时按现有票兜底结算（语义同 Story 分支）
        if get_review_mode() == REVIEW_MODE_MAJORITY:
            approve_n, reject_n = _review_vote_counts(s, "task", t.id)
            if approve_n + reject_n > 0:
                if approve_n > reject_n:
                    _settle_majority_approved(s, t, "task")
                    result["tasks_settled"] += 1
                else:
                    settled = _settle_majority_rejected(s, t, "task")
                    result["tasks_settled"] += 1
                    if settled.status == Status.BLOCKED:
                        result["blocked"] += 1
                continue
        if (t.review_round or 0) >= MAX_REVIEW_ROUNDS:
            # Review 2026-08-26 P1 #1 修复：原 raw SQL 写 blocked 绕过 TaskStateMachine
            # invariant（status_reason 必填 + previous_status 自动维护 + history）。
            # 改用 set_status 把 status transition 收口到唯一 owner。
            try:
                set_status(
                    s, t.id, Status.BLOCKED.value,
                    changed_by=None,
                    reason="timeout max review rounds",
                    status_reason=StatusReason.PENDING_REQUIREMENT_CHANGE.value,
                    cas_predicate=lambda x: x.status == Status.IN_REVIEW,
                )
                result["blocked"] += 1
            except InvalidValue as e:
                # CAS 失败（task 已被其他 worker 改状态）→ 跳过本轮
                s.rollback()
                log.info("scan_review_timeouts: task#%s 状态已被并发改写（%s），跳过", t.id, e)
            except Exception:
                s.rollback()
                log.exception("scan_review_timeouts: task#%s 阻塞失败", t.id)
            continue
        old = t.reviewer_id
        r = s.execute(update(Task).where(
            Task.id == t.id,
            Task.reviewer_id == old,
        ).values(reviewer_id=None))
        if r.rowcount != 1:
            s.rollback()
            continue
        _commit(s)
        fresh = s.get(Task, t.id)
        if fresh is None:
            continue
        new_rev = _reassign_task_reviewer(s, fresh, exclude_user_id=old)
        if new_rev is not None:
            result["tasks_reassigned"] += 1
            result["_tasks_reassigned"].append((fresh.id, new_rev))
        else:
            result["no_candidate"] += 1
    return result


def review_story(s: Session, *, story_id: int, reviewer_user_id: int,
                 verdict: str, comment: str) -> Story:
    """Story 级评审已下线（Ticket 全流程，2026-08-09）。

    评审职责整体下沉 Task 层：design task（in_design→design_pending_review→
    design_review_approved）与实现 task（in_progress→in_review→done）均由
    ``review_task`` / ``assign_task_reviewer`` 承担。
    """
    raise InvalidValue("Story 评审已下线：评审在 Task 层进行（design 评审 / 实现评审）")


def list_agents(s: Session, *, online: bool | None = None, role: str | None = None,
                 order_by_created: bool = False):
    """列出 Agent 池（全局，与项目无关；按需过滤 online/role）。

    2026-08-20 Epic 151 Story 326 Task 1297：MembersTab 文案已明确
    「全局 Agent 池 · 跨项目共享」，故保持全局语义、仅脱敏返回，不按
    project 过滤。``order_by_created=True`` 时按 ``created_at`` 倒序
    （新→旧），与前端展示时间一致。
    """
    expire_stale_agent_heartbeats(s)
    q = s.query(Agent)
    if online is not None:
        q = q.filter(Agent.online == online)
    if role:
        rows = q.order_by(Agent.created_at.desc() if order_by_created else Agent.id.desc()).all()
        return [a for a in rows if role in _parse_json_list(a.roles, "roles")]
    return q.order_by(Agent.created_at.desc() if order_by_created else Agent.id.desc()).all()


# ---------- Story 评审闭环（Epic 122 S1） ----------
MAX_REVIEW_ROUNDS = 5  # 与 Proposal max_rounds 对齐；超限置 blocked 护栏

# ---------- 多数决评审（Epic 122 S3 M3） ----------
REVIEW_MODE_SINGLE = "single"      # 1 名 reviewer，approve 即通过（默认，兼容 S1/S2）
REVIEW_MODE_MAJORITY = "majority"  # N 人投票，达法定票数按多数决结算（文档 #50 §7 决策 #7）
DEFAULT_REVIEW_QUORUM = 3          # 法定票数（env AGENTBOARD_REVIEW_QUORUM 覆盖，2..9）


def assign_task_reviewer(s: Session, task_id: int, *, user_id: int | None = None,
                         is_admin: bool = False) -> Task:
    """随机指派 Task 评审人（幂等；CAS 并发安全）。

    与 Story 版 assign_reviewer 同构：
    - 候选 = 在线 ∩ 角色含 reviewer ∩ 绑定用户属项目成员，且 **≠ assignee**
      （评审人与作者隔离，文档 #51 要求）；
    - CAS 条件 UPDATE ``status=in_review AND reviewer_id IS NULL`` →
      ``reviewer_id=候选``，rowcount=1 才成功；并发下另一个写者获胜时回查返回其指派结果；
    - 幂等：已指派（reviewer_id 非空）直接返回现态，不换人。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.reviewer_id is not None:
        return t  # 幂等：已指派（含 reject 退回后复用同一 reviewer）
    if t.status != Status.IN_REVIEW:
        raise InvalidValue(
            f"task {task_id} is not in_review (current status: {t.status})")
    candidates = _online_reviewer_candidates(s, t.project_id)
    candidates = [a for a in candidates if a.user_id != t.assignee_id]
    if not candidates:
        raise InvalidValue(
            "no online reviewer available (register an online reviewer agent first)")
    ranked = rank_agents_for_task(s, t, role="reviewer", agents=candidates)
    if not ranked:
        raise InvalidValue("no reviewer satisfies the task capability requirements")
    reviewer = ranked[0].agent
    r = s.execute(
        update(Task).where(
            Task.id == task_id,
            Task.reviewer_id.is_(None),
            Task.status == Status.IN_REVIEW,
        ).values(reviewer_id=reviewer.user_id)
    )
    if r.rowcount != 1:
        # 并发写者已抢先指派：回查返回现态
        s.rollback()
        return s.get(Task, task_id)
    _commit(s)
    s.refresh(t)
    return t


def agent_deregister(s: Session, agent_id: str, *, user_id: int | None = None,
                     is_admin: bool = False, probe_message: str = "") -> Agent | None:
    """注销下线：置 online=False（保留注册记录）。Worker probe 失败时带原因。"""
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    if not is_admin and user_id is not None and agent.user_id not in (None, user_id):
        raise InvalidValue("deregister rejected: agent belongs to another user")
    agent.online = False
    if probe_message:
        agent.probe_message = str(probe_message)[:300]
        agent.last_probe_at = utc_now()
    _commit(s); s.refresh(agent); return agent


def create_schedule(s: Session, *, project_id: int, title: str,
                    schedule_type: str = "cron", cron_expr: str | None = None,
                    agent: str | None = None, task_id: int | None = None,
                    task_priority: str | None = None, task_type: str | None = None,
                    epic_id: int | None = None) -> AgentSchedule:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    title = _required(title, "title", 300)
    if schedule_type not in ALL_SCHEDULE_TYPES:
        raise InvalidValue(f"invalid schedule_type '{schedule_type}'")
    if schedule_type == "cron":
        if not cron_expr:
            raise InvalidValue("cron_expr is required for cron schedule")
        _validate_cron(cron_expr)
    else:
        cron_expr = None
    _validate_schedule_filters(
        agent=agent, task_priority=task_priority, task_type=task_type, epic_id=epic_id,
    )
    if task_id is not None and not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    if epic_id is not None and not s.get(Epic, epic_id):
        raise NotFound(f"epic {epic_id} not found")
    sch = AgentSchedule(
        project_id=project_id, title=title,
        schedule_type=schedule_type, cron_expr=cron_expr,
        agent=agent, task_id=task_id,
        task_priority=task_priority, task_type=task_type, epic_id=epic_id,
    )
    s.add(sch); _commit(s); s.refresh(sch); return sch


def claim_story(s: Session, id: int, *, changed_by: int | None = None,
                claimed_by: str = "worker") -> Story:
    """Worker 竞争认领 Story（Ticket 全流程多实例编排）：CAS confirmed → todo。

    多个 Worker 实例（不同 agent CLI）同时扫描同一 confirmed Story 时，
    条件 UPDATE ``status=confirmed`` → ``todo``，rowcount=1 恰一赢家；
    竞争失败抛 IllegalTransition（api 层转 409）。todo 语义 = 已被某 worker
    认领处理中（其它实例扫描 confirmed 不再看到），失败/交接由
    ``unclaim_story`` 回退 confirmed 重新入池。

    认领成功同时写入租约（claimed_by/claimed_at）：持有者进程崩溃后由
    ``reclaim_stale_stories`` 按 claimed_at 过期回收 —— 这是 Story 侧唯一的
    丢单兜底（2026-08-26 前 Story 无租约，崩溃即永久卡 todo）。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == "confirmed")
        .values(
            status="todo",
            claimed_by=(claimed_by or "worker")[:100],
            claimed_at=utc_now(),
        )
    )
    if r.rowcount != 1:
        s.rollback()
        cur = s.get(Story, id)
        if cur is not None and cur.status == "todo":
            raise IllegalTransition(f"story {id} 已被其它 Worker 认领（todo）")
        raise IllegalTransition(f"story {id} 当前状态 {cur.status if cur else '?'}，不可认领")
    _record_story_status_history(s, id, "confirmed", "todo", changed_by=changed_by,
                                 reason="Worker 竞争认领")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st


# Story/Task 认领租约默认时长（与 proposals DEFAULT_CLAIM_LEASE_SECONDS 对齐）。
# 注意：必须显著大于 worker 的 agent_timeout（默认 900s），否则长任务会被
# 其它实例的回收循环误收。worker 启动时对两者关系做告警校验。
DEFAULT_STORY_CLAIM_LEASE_SECONDS = 1800
DEFAULT_TASK_CLAIM_LEASE_SECONDS = 1800


def reclaim_stale_stories(
    s: Session, *, lease_seconds: int = DEFAULT_STORY_CLAIM_LEASE_SECONDS,
) -> list[int]:
    """把租约过期的 todo Story 批量回退 confirmed，返回被回收的 id 列表。

    崩溃兜底：持有 Worker 进程死亡后，其认领的 Story 卡在 todo 永远不会被
    再次扫到（扫描只看 confirmed）。判定依据 ``claimed_at`` 而非 updated_at
    （后者带 onupdate，任何无关写入都会给死租约续期）。

    只回收 claimed_by 非空的行：用户手工置 todo 的 Story 没有 worker 租约，
    不属于本端点管辖。也不做 updated_at 兜底 —— proposals 的 analyzing 是
    worker 专属状态所以可以兜底，Story 的 todo 不是，误收风险大于漏收。
    """
    if lease_seconds < 0:
        raise InvalidValue("lease_seconds must be >= 0")
    cutoff = utc_now() - timedelta(seconds=lease_seconds)
    ids = [
        row[0]
        for row in s.query(Story.id)
        .filter(
            Story.status == "todo",
            Story.claimed_by.isnot(None),
            Story.claimed_by != "",
            Story.claimed_at.isnot(None),
            Story.claimed_at < cutoff,
        )
        .all()
    ]
    if not ids:
        return []
    r = s.execute(
        update(Story)
        .where(
            Story.id.in_(ids),
            Story.status == "todo",
            Story.claimed_at < cutoff,
        )
        .values(status="confirmed", claimed_by="", claimed_at=None)
        .execution_options(synchronize_session=False),
    )
    _commit(s)
    s.expire_all()
    # 并发下原持有者可能恰好 unclaim 成功，按实际 CAS 结果收敛返回集合
    reclaimed = [
        row[0]
        for row in s.query(Story.id)
        .filter(Story.id.in_(ids), Story.status == "confirmed",
                Story.claimed_by == "")
        .all()
    ]
    for sid in reclaimed:
        _record_story_status_history(s, sid, "todo", "confirmed", changed_by=None,
                                     reason="租约到期回收（Worker 崩溃兜底）")
    _commit(s)
    if reclaimed:
        log.warning("reclaim_stale_stories: 回收 %d 条过期租约 story=%s",
                    len(reclaimed), reclaimed)
    return reclaimed


def reclaim_stale_tasks(
    s: Session, *, lease_seconds: int = DEFAULT_TASK_CLAIM_LEASE_SECONDS,
) -> list[int]:
    """把租约过期的 in_progress Task 批量回退 todo，返回被回收的 id 列表。

    与 Story 回收同源（claim_development_task 写租约），但 in_progress 是
    **人机共享状态**，因此额外要求 ``updated_at < cutoff``：认领后若有任何
    后续写入（评审驳回回退、人工改派等），说明工作项仍在活跃流转，一律保护。
    只有「认领后无任何动静且超时」的行才视为持有者已死。

    同样只回收 claimed_by 非空的行 —— 人工认领（assignee 直派 / apply /
    arbitrate）不写租约列，天然不受影响。回收同时清 assignee_id 并释放。
    """
    if lease_seconds < 0:
        raise InvalidValue("lease_seconds must be >= 0")
    cutoff = utc_now() - timedelta(seconds=lease_seconds)
    ids = [
        row[0]
        for row in s.query(Task.id)
        .filter(
            Task.status == Status.IN_PROGRESS,
            Task.claimed_by.isnot(None),
            Task.claimed_by != "",
            Task.claimed_at.isnot(None),
            Task.claimed_at < cutoff,
            Task.updated_at < cutoff,
        )
        .all()
    ]
    if not ids:
        return []
    s.execute(
        update(Task)
        .where(
            Task.id.in_(ids),
            Task.status == Status.IN_PROGRESS,
            Task.claimed_at < cutoff,
            Task.updated_at < cutoff,
        )
        .values(
            status=Status.TODO,
            assignee_id=None,
            status_reason=None,
            previous_status=None,
            claimed_by="",
            claimed_at=None,
        )
        .execution_options(synchronize_session=False),
    )
    _commit(s)
    s.expire_all()
    reclaimed = [
        row[0]
        for row in s.query(Task.id)
        .filter(Task.id.in_(ids), Task.status == Status.TODO,
                Task.claimed_by == "")
        .all()
    ]
    for tid in reclaimed:
        _record_status_history(s, tid, str(Status.IN_PROGRESS), str(Status.TODO),
                               changed_by=None, reason="租约到期回收（Worker 崩溃兜底）")
    _commit(s)
    for tid in reclaimed:
        t = s.get(Task, tid)
        if t is not None:
            _invalidate_project_stats_cache(t.project_id)
    if reclaimed:
        log.warning("reclaim_stale_tasks: 回收 %d 条过期租约 task=%s",
                    len(reclaimed), reclaimed)
    return reclaimed


def get_run(s: Session, id: int) -> AgentRun | None:
    return s.get(AgentRun, id)


def complete_sprint(s: Session, id: int) -> Sprint:
    """完成 Sprint：将其状态改为 completed，未完成任务退回 todo。

    Story 265 修复：直接 SQL UPDATE 必须同时清 status_reason / previous_status，
    否则 blocked 任务退回后会残留「status=todo + status_reason=blocked_reason」
    的不一致状态，违反「非 done/blocked 必清 reason」业务规则。
    """
    sp = s.get(Sprint, id)
    if not sp:
        raise NotFound(f"sprint {id} not found")
    if sp.status == SprintStatus.COMPLETED:
        raise InvalidValue("sprint is already completed")
    sp.status = SprintStatus.COMPLETED
    # 未完成任务退回 todo（backlog 已下线），并清掉残留的 reason / previous_status
    s.query(Task).filter(
        Task.sprint_id == sp.id,
        Task.status.notin_([Status.DONE])
    ).update({
        "sprint_id": None,
        "status": Status.TODO,
        "status_reason": None,
        "previous_status": None,
    })
    _commit(s); s.refresh(sp); return sp


def update_schedule(s: Session, id: int, **fields) -> AgentSchedule | None:
    sch = s.get(AgentSchedule, id)
    if not sch:
        return None
    # 预校验（先于赋值，失败不产生半写）
    if "agent" in fields:
        _validate_schedule_filters(
            agent=fields.get("agent"), task_priority=fields.get("task_priority"),
            task_type=fields.get("task_type"), epic_id=fields.get("epic_id"),
        )
    elif any(k in fields for k in ("task_priority", "task_type", "epic_id")):
        _validate_schedule_filters(
            agent=sch.agent, task_priority=fields.get("task_priority", sch.task_priority),
            task_type=fields.get("task_type", sch.task_type),
            epic_id=fields.get("epic_id", sch.epic_id),
        )
    if "task_id" in fields and fields["task_id"] is not None and not s.get(Task, fields["task_id"]):
        raise NotFound(f"task {fields['task_id']} not found")
    if "epic_id" in fields and fields["epic_id"] is not None and not s.get(Epic, fields["epic_id"]):
        raise NotFound(f"epic {fields['epic_id']} not found")
    for k, v in fields.items():
        if k == "title" and v is not None:
            v = _required(v, "title", 300)
            sch.title = v
        elif k == "schedule_type" and v is not None:
            if v not in ALL_SCHEDULE_TYPES:
                raise InvalidValue(f"invalid schedule_type '{v}'")
            sch.schedule_type = v
        elif k == "cron_expr" and v is not None:
            _validate_cron(v)
            sch.cron_expr = v
        elif k == "enabled" and v is not None:
            sch.enabled = v
        elif k == "next_run_at" and v is not None:
            sch.next_run_at = v
        elif k in ("agent", "task_id", "task_priority", "task_type", "epic_id"):
            # Story 106：显式 null = 解除绑定/清除筛选；已过预校验，直接赋值
            setattr(sch, k, v)
    _commit(s); s.refresh(sch); return sch


def review_task(s: Session, *, task_id: int, reviewer_user_id: int,
                verdict: str, comment: str) -> Task:
    """Task 评审投票（CAS）：仅被指派 reviewer 可操作 in_review 任务。

    - approve：in_review → done（评审通过，任务完成）；
    - reject ：review_round + 1，任务退回 in_progress（开发者修复后重新
      submit-review，reviewer_id 保留 → 同一 reviewer 继续评审）；评论记录意见；
    - 护栏：review_round 达 MAX_REVIEW_ROUNDS → blocked（待人工仲裁）。
    - S3 M3：review_mode=majority 时改为多数决投票（_vote_majority），
      投票人资格放宽为项目在线 reviewer 候选（≠assignee），达法定票数按多数结算。

    Review 2026-08-26 P1 #2 修复：原实现 raw SQL 自带 CAS 条件 + 手写
    status_reason / previous_status / history，绕过 TaskStateMachine 的 invariant
    校验与 side effect（特别是 schedule_judge）。本版本改用 set_status(...)
    把所有状态迁移收口到唯一 owner。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if verdict not in ("approve", "reject"):
        raise InvalidValue(f"invalid verdict '{verdict}' (expected approve|reject)")
    comment = (comment or "").strip()
    if not comment:
        raise InvalidValue("review comment is required (approve/reject must carry a comment)")
    # S3 M3：多数决模式走投票分支（未达法定票数不结算，状态保持）
    if get_review_mode() == REVIEW_MODE_MAJORITY:
        t, _settled = _vote_majority(
            s, t, entity_type="task", reviewer_user_id=reviewer_user_id,
            verdict=verdict, comment=comment)
        return t
    if t.reviewer_id != reviewer_user_id:
        raise InvalidValue("only the assigned reviewer can review this task")
    if t.status != Status.IN_REVIEW:
        raise InvalidValue(f"task is not in_review (current status: {t.status})")
    reviewer = s.get(User, reviewer_user_id)
    reviewer_name = reviewer.display_name or reviewer.username if reviewer else f"user#{reviewer_user_id}"

    # Review 2026-08-26 P1 #2：CAS 条件提到 set_status.cas_predicate
    # （保留并发安全），状态迁移走 set_status → TaskStateMachine → schedule_judge
    cas = lambda x: (x.reviewer_id == reviewer_user_id and x.status == Status.IN_REVIEW)
    if verdict == "approve":
        set_status(
            s, task_id, Status.DONE.value,
            changed_by=reviewer_user_id,
            reason="review approve",
            status_reason=StatusReason.COMPLETED.value,
            cas_predicate=cas,
        )
    else:  # reject
        new_round = (t.review_round or 0) + 1
        # review_round 是 task 自身的字段，不属于 TaskStateMachine transition 边
        # 单独 update；blocked / in_progress 两种 reject 路径都要 +1（不区分）
        t.review_round = new_round
        if new_round >= MAX_REVIEW_ROUNDS:
            set_status(
                s, task_id, Status.BLOCKED.value,
                changed_by=reviewer_user_id,
                reason=f"review reject round={new_round}",
                status_reason=StatusReason.PENDING_REQUIREMENT_CHANGE.value,
                cas_predicate=cas,
            )
        else:
            set_status(
                s, task_id, Status.IN_PROGRESS.value,
                changed_by=reviewer_user_id,
                reason=f"review reject round={new_round}",
                cas_predicate=cas,
            )

    # 评审意见落评论（唯一载体）
    create_comment(s, author=reviewer_name, content=comment, task_id=task_id)
    s.refresh(t)
    # 注：set_status 已调过 finalize_task_assignment / _record_learning_outcome /
    # schedule_judge（终态时），这里只补一条 project stats 缓存失效用于 UI 同步。
    _invalidate_project_stats_cache(t.project_id)
    return t


def get_schedule(s: Session, id: int) -> AgentSchedule | None:
    return s.get(AgentSchedule, id)


def submit_task_for_review(s: Session, task_id: int, *, user_id: int,
                           is_admin: bool = False) -> Task:
    """开发完成提交评审（Epic 122 切片 2 M1）。

    - 校验 status == in_progress（开发态才可提交评审）；
    - assignee 匹配（admin 豁免）：非认领者提交 → 明确错误；
    - 通过 set_status 走合法迁移 in_progress → in_review，事件源由 API 层广播。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status != Status.IN_PROGRESS:
        raise InvalidValue(
            f"task {task_id} is not in_progress (current status: {t.status})")
    if not is_admin and t.assignee_id != user_id:
        raise InvalidValue(
            f"task {task_id} is assigned to user#{t.assignee_id}, "
            "only the assignee (or admin) can submit for review")
    return set_status(s, task_id, Status.IN_REVIEW)


# ---------- Task 评审闭环（Epic 122 切片 2 M2） ----------

def report_run_result(s: Session, id: int, *, status: str, summary: str | None = None,
                      log_ref: str | None = None) -> AgentRun:
    """
    Agent 主动报告一次 run 的最终结果（Story 104）。

    - 仅 pending/running 可迁移到终态；终态不可再变（防重放覆盖）；
    - 幂等：终态重复报告相同 status 直接返回现有值（不抛错）；
    - 落库 summary/log_ref + finished_at。
    """
    run = s.get(AgentRun, id)
    if not run:
        raise NotFound(f"run {id} not found")
    if status not in ALL_RUN_STATUSES:
        raise InvalidValue(f"invalid run status '{status}'")
    if status not in RUN_TRANSITIONS.get(run.status, set()):
        if run.status == status:
            # 幂等：重复报告同一终态，仅补齐缺失的 summary/log_ref
            if summary is not None and run.summary is None:
                run.summary = summary
            if log_ref is not None and run.log_ref is None:
                run.log_ref = log_ref
            _commit(s); s.refresh(run); return run
        raise IllegalTransition(f"run status {run.status} -> {status} 不合法")
    run.status = status
    if summary is not None:
        run.summary = summary
    if log_ref is not None:
        run.log_ref = log_ref
    if run.finished_at is None:
        run.finished_at = utc_now()
    _commit(s); s.refresh(run); return run


def claim_development_task(s: Session, task_id: int, *, user_id: int,
                           claimed_by: str = "worker") -> Task:
    """开发任务竞争认领（Epic 122 切片 2 M1，CAS 并发安全；Story 265 后仅认领 todo）。

    - 条件 UPDATE ``status = todo`` → ``in_progress + assignee_id=user_id``，
      rowcount=1 才成功；并发下另一个写者获胜 → 明确错误（含现状）；
    - 复用 Epic 118 护栏语义：已认领（in_progress/in_review 等）或已结束（done/blocked）
      的任务拒绝重复认领，不创建 Run、不改状态；
    - 认领是「系统操作」，绕开 TRANSITIONS 常规校验；
    - Story 265 收敛：仅 todo 可认领（backlog 已下线，旧 backlog 数据由迁移脚本归并到 todo）。

    认领成功同时写入租约（claimed_by/claimed_at）：持有者崩溃后由
    ``reclaim_stale_tasks`` 回收。人工 set_status/apply 路径不写这两列，
    因此回收只影响 agent 认领的行。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status != Status.TODO:
        raise InvalidValue(
            f"task {task_id} already claimed or not claimable (status={t.status})")
    old_status = t.status
    now = utc_now()
    r = s.execute(
        update(Task).where(
            Task.id == task_id,
            Task.status == Status.TODO,
        ).values(
            status=Status.IN_PROGRESS,
            assignee_id=user_id,
            claimed_by=(claimed_by or "worker")[:100],
            claimed_at=now,
        )
    )
    if r.rowcount != 1:
        s.rollback()
        cur = s.get(Task, task_id)
        raise InvalidValue(
            f"task {task_id} claim conflict: already claimed "
            f"(status={cur.status if cur else 'deleted'})")
    _record_status_history(s, task_id, str(old_status), str(Status.IN_PROGRESS),
                           changed_by=user_id, reason="claim")
    _commit(s)
    s.refresh(t)
    _invalidate_project_stats_cache(t.project_id)
    return t


def complete_story(s: Session, id: int, *, changed_by: int | None = None,
                   reason: str = "") -> Story:
    """Story 自动收尾（Ticket 全流程）：任意非 done/blocked 状态 → done（CAS）。

    Worker 在 Story 下全部 task done 后调用本入口收尾，绕开常规迁移表
    （agent 推进中间态后可能停在 confirmed/todo/in_progress，直接置 done
    不在 TRANSITIONS 出边内）。blocked 不自动收尾（人工仲裁态）。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    if st.status == "done":
        s.refresh(st); return st
    if st.status == "blocked":
        raise IllegalTransition(f"story {id} 处于 blocked，禁止自动收尾（需人工仲裁）")
    old = st.status
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == old)
        .values(status="done", claimed_by="", claimed_at=None)
    )
    if r.rowcount != 1:
        s.rollback()
        raise IllegalTransition("complete 冲突：Story 状态已被并发修改")
    _record_story_status_history(s, id, old, "done", changed_by=changed_by,
                                 reason=reason or "全部任务完成，自动收尾")
    _commit(s)
    s.refresh(st)
    epic = s.get(Epic, st.epic_id)
    if epic is not None:
        _invalidate_project_stats_cache(epic.project_id)
    return st


def agent_heartbeat(s: Session, agent_id: str, *, user_id: int | None = None,
                    probe_ok: bool | None = None,
                    probe_message: str = "") -> Agent | None:
    """心跳保活：置 online（probe_ok=None 时默认 True）并刷新 last_heartbeat。

    Worker probe 路径传 probe_ok/probe_message 落 probe 详情（前端展示）；
    Agent 自报心跳路径（MCP）不带，仅刷新 last_heartbeat。
    """
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    if user_id is not None and agent.user_id not in (None, user_id):
        raise InvalidValue("heartbeat rejected: agent belongs to another user")
    agent.online = True if probe_ok is None else probe_ok
    agent.last_heartbeat = utc_now()
    if probe_message:
        agent.probe_message = str(probe_message)[:300]
        agent.last_probe_at = utc_now()
    if user_id is not None and agent.user_id is None:
        agent.user_id = user_id
    _commit(s); s.refresh(agent); return agent


# ---------- Worker + AgentInstance（2026-08-26 P1：多 Worker 部署隔离） ----------

WORKER_STATUSES = {"active", "inactive"}


def _sync_agent_online(s: Session, agent_id: str) -> None:
    """聚合 ``Agent.online = ANY(instance.online for that agent)``。

    在 ``instance_heartbeat`` / ``instance_deregister`` 内调用，保证 ``Agent.online``
    与各 instance 状态一致。任一 instance online → Agent.online = true；全 offline
    → false。评审/调度继续读 ``Agent.online``，不破坏现有逻辑。

    **独立 commit**：本函数在 ``instance_heartbeat`` 等已经 ``_commit`` 之后调用，
    但 agent.online 同步是独立关注点，独立 commit 保证外部 ``s.refresh()`` 能读到
    最新值（多测试 / 跨调用场景）。
    """
    expire_stale_agent_heartbeats(s)
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return
    any_online = s.query(AgentInstance).filter(
        AgentInstance.agent_id == agent_id,
        AgentInstance.online == True,  # noqa: E712
    ).first() is not None
    if agent.online != any_online:
        agent.online = any_online
        _commit(s)


def register_worker(s: Session, *, worker_id: str, hostname: str = "",
                    status: str = "active") -> Worker:
    """注册/更新 Worker（幂等）。Worker 启动时自报入口。"""
    worker_id = _required(worker_id, "worker_id", 64)
    if status not in WORKER_STATUSES:
        raise InvalidValue(f"status must be one of {sorted(WORKER_STATUSES)}")
    existing = s.query(Worker).filter(Worker.worker_id == worker_id).first()
    if existing:
        existing.hostname = (hostname or "")[:200]
        if status != existing.status:
            existing.status = status
        existing.last_heartbeat = utc_now()
        _commit(s); s.refresh(existing); return existing
    w = Worker(
        worker_id=worker_id,
        hostname=(hostname or "")[:200],
        status=status,
        last_heartbeat=utc_now(),
    )
    s.add(w)
    try:
        _commit(s); s.refresh(w); return w
    except Duplicate:
        s.rollback()
        return s.query(Worker).filter(Worker.worker_id == worker_id).first()


def get_worker_by_id(s: Session, worker_id: str) -> Worker | None:
    return s.query(Worker).filter(Worker.worker_id == worker_id).first()


def list_workers(s: Session) -> list[Worker]:
    return s.query(Worker).order_by(Worker.id.asc()).all()


def upsert_agent_instance(
    s: Session, *,
    worker_id: str,
    agent_id: str,
    cli_command: str = "",
    model: str = "",
    auth_key: str = "",
    enabled: bool = True,
) -> AgentInstance:
    """Worker 在本机挂载/更新一个 ``(worker_id, agent_id)`` instance（幂等）。

    - ``worker_id`` 必须已存在（先调 ``register_worker``）；
    - ``agent_id`` 必须已注册（``Agent`` 表里存在）；
    - ``cli_command`` 走 ``validate_cli_command`` 安全校验（B-A2）。
    """
    worker_id = _required(worker_id, "worker_id", 64)
    agent_id = _required(agent_id, "agent_id", 64)
    if not get_worker_by_id(s, worker_id):
        raise NotFound(f"worker {worker_id} not found (register it first)")
    if not s.query(Agent).filter(Agent.agent_id == agent_id).first():
        raise NotFound(f"agent {agent_id} not found")
    validate_cli_command(cli_command)
    existing = s.query(AgentInstance).filter(
        AgentInstance.worker_id == worker_id,
        AgentInstance.agent_id == agent_id,
    ).first()
    if existing:
        existing.cli_command = (cli_command or "")[:500]
        existing.model = (model or "")[:100]
        existing.auth_key = (auth_key or "")[:100]
        existing.enabled = bool(enabled)
        _commit(s); s.refresh(existing); return existing
    inst = AgentInstance(
        worker_id=worker_id,
        agent_id=agent_id,
        cli_command=(cli_command or "")[:500],
        model=(model or "")[:100],
        auth_key=(auth_key or "")[:100],
        enabled=bool(enabled),
    )
    s.add(inst)
    try:
        _commit(s); s.refresh(inst); return inst
    except Duplicate:
        # 并发：回查返回既有
        s.rollback()
        return s.query(AgentInstance).filter(
            AgentInstance.worker_id == worker_id,
            AgentInstance.agent_id == agent_id,
        ).first()


def get_agent_instance(s: Session, instance_id: int) -> AgentInstance | None:
    return s.get(AgentInstance, instance_id)


def list_agent_instances(
    s: Session, *,
    worker_id: str | None = None,
    agent_id: str | None = None,
) -> list[AgentInstance]:
    """列 AgentInstance。``worker_id`` 非空时按 owner 视角返回（上层决定是否暴露
    ``cli_command`` —— ``to_owner_dict`` 包含 CLI，``to_public_dict`` 脱敏）。"""
    expire_stale_agent_heartbeats(s)
    q = s.query(AgentInstance)
    if worker_id:
        q = q.filter(AgentInstance.worker_id == worker_id)
    if agent_id:
        q = q.filter(AgentInstance.agent_id == agent_id)
    return q.order_by(AgentInstance.id.asc()).all()


def delete_agent_instance(s: Session, instance_id: int) -> bool:
    inst = s.get(AgentInstance, instance_id)
    if not inst:
        return False
    s.delete(inst)
    _commit(s)
    _sync_agent_online(s, inst.agent_id)
    return True


def instance_heartbeat(
    s: Session, instance_id: int, *,
    caller_worker_id: str,
    probe_ok: bool | None = None,
    probe_message: str = "",
) -> AgentInstance:
    """Instance 心跳保活（Worker 探测成功后调用）。

    **强制 ownership 校验**（2026-08-26 P1 修复）：``caller_worker_id`` 必传，
    ``instance.worker_id != caller_worker_id`` 抛 ``Forbidden``。这是防 A 覆盖
    B 健康 instance 的关键闸门 —— 不允许空字符串绕过（避免 caller 不带
    worker_id 调通，从而任意改全表）。
    """
    if not caller_worker_id:
        raise InvalidValue("caller_worker_id is required for instance ownership check")
    inst = s.get(AgentInstance, instance_id)
    if not inst:
        raise NotFound(f"agent_instance {instance_id} not found")
    if inst.worker_id != caller_worker_id:
        raise Forbidden(
            f"instance {instance_id} belongs to worker {inst.worker_id!r}, "
            f"not {caller_worker_id!r}"
        )
    inst.online = True if probe_ok is None else bool(probe_ok)
    inst.last_heartbeat = utc_now()
    if probe_message:
        inst.probe_message = str(probe_message)[:300]
        inst.last_probe_at = utc_now()
    # 同步所属 Worker 的 last_heartbeat
    w = get_worker_by_id(s, inst.worker_id)
    if w is not None:
        w.last_heartbeat = utc_now()
    _commit(s); s.refresh(inst)
    _sync_agent_online(s, inst.agent_id)
    return inst


def instance_deregister(
    s: Session, instance_id: int, *,
    caller_worker_id: str,
    probe_message: str = "",
) -> AgentInstance:
    """Instance 注销下线（Worker 探测失败后调用）。同 :func:`instance_heartbeat` 校验 ownership。"""
    if not caller_worker_id:
        raise InvalidValue("caller_worker_id is required for instance ownership check")
    inst = s.get(AgentInstance, instance_id)
    if not inst:
        raise NotFound(f"agent_instance {instance_id} not found")
    if inst.worker_id != caller_worker_id:
        raise Forbidden(
            f"instance {instance_id} belongs to worker {inst.worker_id!r}, "
            f"not {caller_worker_id!r}"
        )
    inst.online = False
    if probe_message:
        inst.probe_message = str(probe_message)[:300]
        inst.last_probe_at = utc_now()
    _commit(s); s.refresh(inst)
    _sync_agent_online(s, inst.agent_id)
    return inst


def list_schedules(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(AgentSchedule).filter(AgentSchedule.project_id == project_id)
    return _paginate(q, limit, offset).all()




# ---- 同步自 service.py ----
def _validate_cron(expr: str) -> None:
    """校验 cron 表达式格式（5 字段：分 时 日 月 周）。"""
    if not _CRON_PATTERN.match(expr.strip()):
        raise InvalidValue(f"invalid cron expression: {expr}")

# ---- 同步自 service.py ----
def _validate_schedule_filters(*, agent, task_priority, task_type, epic_id) -> None:
    """校验 AgentSchedule 绑定/筛选字段（None = 不设，均合法）。"""
    if agent is not None and agent not in SCHEDULE_AGENTS:
        raise InvalidValue(
            f"invalid agent '{agent}', must be one of {', '.join(SCHEDULE_AGENTS)}"
        )
    if task_priority is not None and task_priority not in ALL_PRIORITIES:
        raise InvalidValue(f"invalid task_priority '{task_priority}'")
    if task_type is not None and task_type not in ALL_TYPES:
        raise InvalidValue(f"invalid task_type '{task_type}'")

# ---- 同步自 service.py ----
def pick_eligible_task(s: Session, schedule: AgentSchedule):
    """
    为「项目/Agent 级」schedule 挑选下一个 eligible task。

    规则：
    - 固定 ``task_id`` → 直接返回该 task（存在即返回，兼容旧单任务语义）；
    - 项目级：``status ∈ (todo,)``（Story 265 backlog 已下线，仅 todo 仍 eligible），
      按 ``epic_id`` / ``task_type`` 过滤，
      ``task_priority`` 为**最低门槛**（≥ 该优先级才 eligible），
      结果按优先级降序 + id 升序取第一个；
    - 无匹配返回 None（调用方跳过本次触发）。

    Returns:
        Task | None
    """
    if schedule.task_id is not None:
        return s.get(Task, schedule.task_id)
    q = s.query(Task).filter(
        Task.project_id == schedule.project_id,
        Task.status.in_(ELIGIBLE_TASK_STATUSES),
    )
    if schedule.epic_id is not None:
        # Task 不直接挂 epic_id，经 story 归属过滤
        q = q.filter(
            Task.story_id.in_(
                s.query(Story.id).filter(Story.epic_id == schedule.epic_id)
            )
        )
    if schedule.task_type is not None:
        q = q.filter(Task.type == schedule.task_type)
    if schedule.task_priority is not None:
        threshold = PRIORITY_RANK[schedule.task_priority]
        eligible_priorities = [
            p for p, rank in PRIORITY_RANK.items() if rank >= threshold
        ]
        q = q.filter(Task.priority.in_(eligible_priorities))
    # 优先级降序（highest 优先）+ id 升序（稳定、可预测）
    from sqlalchemy import case
    rank_case = case(
        *[(Task.priority == p, r) for p, r in PRIORITY_RANK.items()],
        else_=0,
    )
    return q.order_by(rank_case.desc(), Task.id.asc()).first()

# ---- 同步自 service.py ----
def get_review_mode() -> str:
    """评审模式：环境变量 AGENTBOARD_REVIEW_MODE（single|majority），非法回退 single。"""
    mode = os.environ.get("AGENTBOARD_REVIEW_MODE", "").strip().lower()
    return mode if mode in (REVIEW_MODE_SINGLE, REVIEW_MODE_MAJORITY) else REVIEW_MODE_SINGLE

# ---- 同步自 service.py ----
def get_review_quorum() -> int:
    """法定票数：AGENTBOARD_REVIEW_QUORUM（2..9），非法/缺省回退 3。"""
    raw = os.environ.get("AGENTBOARD_REVIEW_QUORUM", "").strip()
    try:
        q = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_QUORUM
    return q if 2 <= q <= 9 else DEFAULT_REVIEW_QUORUM

# ---- 同步自 service.py ----
def _is_reviewer_candidate(s: Session, project_id: int, user_id: int,
                           exclude_user_id: int | None = None) -> bool:
    """投票人校验（majority 模式）：在线 ∩ reviewer 角色 ∩ 项目成员 ∩ ≠exclude。

    与分配器候选集同源（_online_reviewer_candidates），保证只有能被指派为
    reviewer 的 Agent 才能参与多数决投票（评审强度升级，但参与者资格不变）。
    """
    if exclude_user_id is not None and user_id == exclude_user_id:
        return False
    for a in _online_reviewer_candidates(s, project_id):
        if a.user_id == user_id:
            return True
    return False

# ---- 同步自 service.py ----
def _upsert_review_vote(s: Session, *, entity_type: str, entity_id: int,
                        reviewer_user_id: int, verdict: str,
                        comment_id: int | None, round: int) -> None:
    """一人一票 upsert：存在则更新 verdict/comment（改票），否则插入。

    双后端兼容：先查后写（量级小，避免方言差异的 ON CONFLICT 语法）。
    """
    existing = s.query(ReviewVote).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
        ReviewVote.reviewer_user_id == reviewer_user_id,
    ).first()
    if existing is not None:
        existing.verdict = verdict
        existing.comment_id = comment_id
        existing.round = round
        _commit(s)
        return
    s.add(ReviewVote(entity_type=entity_type, entity_id=entity_id,
                     reviewer_user_id=reviewer_user_id, verdict=verdict,
                     comment_id=comment_id, round=round))
    _commit(s)

# ---- 同步自 service.py ----
def _review_vote_counts(s: Session, entity_type: str, entity_id: int) -> tuple[int, int]:
    """返回 (approve, reject) 票数。"""
    rows = s.query(ReviewVote.verdict, func.count(ReviewVote.id)).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).group_by(ReviewVote.verdict).all()
    counts = dict(rows)
    return int(counts.get("approve", 0)), int(counts.get("reject", 0))

# ---- 同步自 service.py ----
def _clear_review_votes(s: Session, entity_type: str, entity_id: int) -> None:
    """结算后清票（终态 / 驳回后开新一轮，MVP 简化：历史票不跨轮保留）。"""
    s.query(ReviewVote).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).delete(synchronize_session=False)
    _commit(s)

# ---- 同步自 service.py ----
def _settle_majority_approved(s: Session, entity, entity_type: str):
    """多数通过（CAS）：Story pending_review→ready / Task in_review→done，结算后清票。"""
    if entity_type == "story":
        r = s.execute(update(Story).where(
            Story.id == entity.id,
            Story.status == "pending_review",
        ).values(status="ready"))
    else:
        r = s.execute(update(Task).where(
            Task.id == entity.id,
            Task.status == Status.IN_REVIEW,
        ).values(status=Status.DONE, status_reason="completed"))
    if r.rowcount != 1:
        s.rollback()
        raise InvalidValue("review conflict: entity state changed concurrently")
    if entity_type == "task":
        _record_status_history(s, entity.id, str(Status.IN_REVIEW), str(Status.DONE),
                               reason="majority approve")
    _commit(s)
    _clear_review_votes(s, entity_type, entity.id)
    settled = s.get(type(entity), entity.id)
    if entity_type == "task":
        finalize_task_assignment(s, settled)
        _record_learning_outcome(s, settled)
    return settled

# ---- 同步自 service.py ----
def _settle_majority_rejected(s: Session, entity, entity_type: str):
    """多数驳回：review_round+1，Story 回 pending_review / Task 回 in_progress；
    达 MAX_REVIEW_ROUNDS → blocked 护栏；结算后清票（下一轮重新投票）。
    """
    new_round = (entity.review_round or 0) + 1
    if entity_type == "story":
        target = "blocked" if new_round >= MAX_REVIEW_ROUNDS else "pending_review"
        r = s.execute(update(Story).where(
            Story.id == entity.id,
            Story.status == "pending_review",
        ).values(review_round=new_round, status=target))
    else:
        target = Status.BLOCKED if new_round >= MAX_REVIEW_ROUNDS else Status.IN_PROGRESS
        r = s.execute(update(Task).where(
            Task.id == entity.id,
            Task.status == Status.IN_REVIEW,
        ).values(
            review_round=new_round,
            status=target,
            status_reason=(
                "pending_requirement_change"
                if target == Status.BLOCKED else None
            ),
            previous_status=(
                str(Status.IN_REVIEW) if target == Status.BLOCKED else None
            ),
        ))
    if r.rowcount != 1:
        s.rollback()
        raise InvalidValue("review conflict: entity state changed concurrently")
    if entity_type == "task":
        _record_status_history(s, entity.id, str(Status.IN_REVIEW), str(target),
                               reason=f"majority reject round={new_round}")
    _commit(s)
    _clear_review_votes(s, entity_type, entity.id)
    settled = s.get(type(entity), entity.id)
    if entity_type == "task" and settled.status == Status.BLOCKED:
        finalize_task_assignment(s, settled)
        _record_learning_outcome(s, settled)
    return settled

# ---- 同步自 service.py ----
def _vote_majority(s: Session, entity, *, entity_type: str, reviewer_user_id: int,
                   verdict: str, comment: str):
    """多数决投票（S3 M3）：写票（一人一票 upsert）→ 达法定票数结算。

    - 权限：投票人须是该项目在线 reviewer 候选（与分配器同源）；
      Task 版额外排除 assignee（评审人与作者隔离）；
    - 未达 quorum：状态保持（pending_review / in_review），评论照记，
      返回 (entity, settled=False)；
    - 达 quorum：approve > reject → 通过；reject >= approve（含平局保守驳回）
      → 驳回（round+1，回原评审流/开发流）；返回 (entity, settled=True)。
    """
    if entity_type == "story":
        epic = s.get(Epic, entity.epic_id)
        if epic is None:
            raise NotFound(f"epic {entity.epic_id} not found")
        project_id = epic.project_id
        expected_status = "pending_review"
        exclude = None
    else:
        project_id = entity.project_id
        expected_status = Status.IN_REVIEW
        exclude = entity.assignee_id
    if not _is_reviewer_candidate(s, project_id, reviewer_user_id,
                                  exclude_user_id=exclude):
        raise InvalidValue(
            "only an online reviewer agent of this project can vote (majority mode)")
    if entity.status != expected_status:
        raise InvalidValue(
            f"entity is not {expected_status} (current status: {entity.status})")
    reviewer = s.get(User, reviewer_user_id)
    reviewer_name = reviewer.display_name or reviewer.username if reviewer else f"user#{reviewer_user_id}"
    # 评审意见落评论（唯一载体，与 single 模式一致）
    comment_obj = create_comment(
        s, author=reviewer_name, content=comment,
        **({f"{entity_type}_id": entity.id}))
    _upsert_review_vote(s, entity_type=entity_type, entity_id=entity.id,
                        reviewer_user_id=reviewer_user_id, verdict=verdict,
                        comment_id=comment_obj.id, round=entity.review_round or 0)
    approve_n, reject_n = _review_vote_counts(s, entity_type, entity.id)
    if approve_n + reject_n < get_review_quorum():
        s.refresh(entity)
        return entity, False
    if approve_n > reject_n:
        return _settle_majority_approved(s, entity, entity_type), True
    return _settle_majority_rejected(s, entity, entity_type), True

# ---- 同步自 service.py ----
def _online_reviewer_candidates(s: Session, project_id: int) -> list[Agent]:
    """在线 ∩ 角色含 reviewer ∩ 绑定 user 属项目成员 的 Agent 候选集。"""
    expire_stale_agent_heartbeats(s)
    member_ids = {
        r[0] for r in s.query(ProjectMember.user_id).filter(
            ProjectMember.project_id == project_id
        ).all()
    }
    online_agents = s.query(Agent).filter(Agent.online == True).all()  # noqa: E712
    candidates = []
    for a in online_agents:
        if a.user_id not in member_ids:
            continue
        if "reviewer" in _parse_json_list(a.roles, "roles"):
            candidates.append(a)
    return candidates

# ---- 同步自 service.py ----
def assign_reviewer(s: Session, story_id: int, *, user_id: int | None = None,
                    is_admin: bool = False) -> Story:
    """Story 级评审已下线（Ticket 全流程，2026-08-09）。

    评审职责整体下沉 Task 层（design task 的 in_design 评审流 / 实现 task 的
    in_review 评审）。调用方应改用 Task 的 ``assign_task_reviewer``。
    """
    raise InvalidValue("Story 评审已下线：评审在 Task 层进行（design 评审 / 实现评审）")

# ---- 同步自 service.py ----
def list_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的评审任务（Story，按 pending_review 优先排序）。"""
    q = s.query(Story).filter(Story.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES and status not in STORY_REVIEW_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Story.status == status)
    q = q.order_by(Story.status.desc(), Story.id.desc())
    return q.all()

# ---- 同步自 service.py ----
def list_task_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的 Task 评审任务（按 in_review 优先排序）。"""
    q = s.query(Task).filter(Task.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Task.status == status)
    q = q.order_by(Task.status.desc(), Task.id.desc())
    return q.all()

# ---- 同步自 service.py ----
def _reassign_story_reviewer(s: Session, story: Story,
                             exclude_user_id: int | None = None) -> int | None:
    """Story 超时重派：候选排除旧 reviewer，CAS（pending_review AND reviewer_id IS NULL）。

    调用前 reviewer 必须已解绑（CAS 由调用方仲裁）；候选为空 → None（保持解绑，
    由下轮轮询补派，评审流不因重派失败而卡死）。成功返回新 reviewer 的 user_id。
    """
    epic = s.get(Epic, story.epic_id)
    if epic is None:
        return None
    candidates = _online_reviewer_candidates(s, epic.project_id)
    candidates = [a for a in candidates if a.user_id != exclude_user_id]
    if not candidates:
        return None
    reviewer = sorted(candidates, key=lambda candidate: candidate.id)[0]
    r = s.execute(
        update(Story).where(
            Story.id == story.id,
            Story.reviewer_id.is_(None),
            Story.status == "pending_review",
        ).values(reviewer_id=reviewer.user_id)
    )
    if r.rowcount != 1:
        s.rollback()
        return None
    _commit(s)
    return reviewer.user_id

# ---- 同步自 service.py ----
def _reassign_task_reviewer(s: Session, task: Task,
                            exclude_user_id: int | None = None) -> int | None:
    """Task 超时重派：候选排除旧 reviewer 与 assignee（评审人/作者隔离），CAS。

    成功返回新 reviewer 的 user_id；候选为空 / CAS 失败 → None。
    """
    candidates = _online_reviewer_candidates(s, task.project_id)
    candidates = [a for a in candidates
                  if a.user_id not in (exclude_user_id, task.assignee_id)]
    if not candidates:
        return None
    ranked = rank_agents_for_task(s, task, role="reviewer", agents=candidates)
    if not ranked:
        return None
    reviewer = ranked[0].agent
    r = s.execute(
        update(Task).where(
            Task.id == task.id,
            Task.reviewer_id.is_(None),
            Task.status == Status.IN_REVIEW,
        ).values(reviewer_id=reviewer.user_id)
    )
    if r.rowcount != 1:
        s.rollback()
        return None
    _commit(s)
    return reviewer.user_id

# ---- 同步自 service.py ----
def _story_last_activity(s: Session, story: Story) -> datetime:
    """Story 最后活动 = max(created_at, 最新评论时间)；无评论回退 created_at。

    评审意见唯一载体是评论（评论往返即活动），Story 无 updated_at 列，
    用评论时间作为「卡住多久」的代理指标（零迁移方案）。
    """
    last_comment = s.query(func.max(Comment.created_at)).filter(
        Comment.story_id == story.id
    ).scalar()
    if last_comment is not None and last_comment > story.created_at:
        return last_comment
    return story.created_at
