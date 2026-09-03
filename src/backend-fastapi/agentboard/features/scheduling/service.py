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
import random
import re as _re
from dataclasses import dataclass, field
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
    expire_stale_worker_heartbeats,
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
from ...core.common.enums import Status as TaskStatus  # noqa: E402 — 跨域状态枚举
from ..work_items.models import Comment  # noqa: E402 — 评审意见评论实体
from ..projects.models import STORY_REVIEW_STATUSES  # noqa: E402 — Story 级评审态（恒空占位）

from .models import (
    DEFAULT_REVIEW_QUORUM,
    DEFAULT_REVIEW_TIMEOUT_MINUTES,
    DEFAULT_TIMEOUT_SCAN_BATCH,
    MAX_REVIEW_ROUNDS,
    REVIEW_MODE_MAJORITY,
    REVIEW_MODE_SINGLE,
    TaskAssignment,
)


# 评审超时(30 分钟,任务超过这个时间还没人评审就重新指派)
DEFAULT_REVIEW_TIMEOUT_MINUTES = 30
DEFAULT_TIMEOUT_SCAN_BATCH = 20
# 注：DEFAULT_REVIEW_QUORUM / REVIEW_MODE_* / MAX_REVIEW_ROUNDS 不再在此重复
# 定义，真源见 scheduling/models.py（本文件顶部已 import）。
# 2026-09-02 收敛（T1.2 + Plan §六-4 R6）：改这些常量只需改 models.py 一处。


# Agent 在 projects.models
from ..projects.models import Project
from ..work_items.models import Task, TaskDependency
from ..work_items.ownership import (  # noqa: E402 — T1.5 统一执行门（只依赖 model 层，不成环）
    CODE_NO_OWNER, agent_can_handle_work_item, work_item_owner_user_id,
)
from ...agent_registry_cache import (  # noqa: E402 — T4.1 ephemeral presence 求交
    ephemeral_agents_enabled, get_default_cache,
)
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
        # P0 (workflow-outbox-2026-08 follow-up): the previous code path
        # synthesized a fake Agent row here (cli_command="", auth_key="",
        # online=False) so the assignment would "succeed" and the task
        # would advance to in_progress. That left the worker side with
        # no real CLI / OAuth state and the task deadlocked forever.
        # The new boundary is: the worker is the source of truth for
        # agent configuration; the server only ever references agents
        # that already exist. ``agent-ephemeral-2026-09`` decision G
        # makes ``agents`` / ``agent_instances`` read-only on the
        # server side. Refuse to mint a row and let the caller fail
        # loud so an operator registers the agent first.
        raise NotFound(
            f"agent '{agent_id}' is not registered; "
            "create_run refuses to mint a synthetic Agent. "
            "Register the agent via POST /api/agents first "
            "(or wait for the worker's WSS HELLO to publish it)."
        )
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
        # Story 级 majority 兜底结算已移除（2026-09-02 T0.1c）：Story 评审自
        # 2026-08-09 起下线，Story 不会再进入 pending_review + 已指派 reviewer
        # 的组合，该段为死路径（且 _settle_* 已不再支持 entity_type="story"）。
        # result["stories_settled"] 字段保留以兼容 API 响应，恒为 0。
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
# MAX_REVIEW_ROUNDS 真源也在 scheduling/models.py（原在此处第二份定义，
# 2026-09-02 按 Plan §六-4 收敛）。

# ---------- 多数决评审（Epic 122 S3 M3） ----------
# REVIEW_MODE_* / DEFAULT_REVIEW_QUORUM 真源在 scheduling/models.py
# （本文件顶部 from .models import ...），此处不再重复定义。


def assign_task_reviewer(s: Session, task_id: int, *, user_id: int | None = None,
                         is_admin: bool = False,
                         count: int = 1) -> Task:
    """随机指派 Task 评审人（幂等；CAS 并发安全；支持多数决 fan-out）。

    与 Story 版 assign_reviewer 同构：
    - 候选 = 在线可运行实例 ∩ 绑定用户属于项目成员 ∩ 能力匹配，且
      **不是本 Task 的实现者**（评审人与作者隔离，文档 #51 要求）；
    - CAS 条件 UPDATE ``status=in_review AND reviewer_id IS NULL`` →
      ``reviewer_id=候选``，rowcount=1 才成功；并发下另一个写者获胜时回查返回其指派结果；
    - 幂等：已指派（reviewer_id 非空）直接返回现态，不换人。
    - **Sprint 12 多数决 fan-out**（count > 1）：首位 reviewer 仍写入
      ``task.reviewer_id``（向后兼容旧查询 "reviewer_id IS NULL 即未指派"），
      后续 N-1 位写入 ``review_votes`` 的 NULL verdict 占位行——投票时再
      落 approve/reject。已有 ``(entity_type, entity_id, reviewer_agent_id)``
      唯一约束天然防重（per-agent 一票），且每次都从「排除已选」后的候选池
      重排，避免同一 reviewer agent 被重复指派。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status != Status.IN_REVIEW:
        raise InvalidValue(
            f"task {task_id} is not in_review (current status: {t.status})")
    # 上限硬卡：避免误把 100 传进 fan-out 把整组 reviewer 一次耗光。
    # 9 = AGENTBOARD_REVIEW_QUORUM 最大允许（get_review_quorum 的上界）。
    if count < 1 or count > 9:
        raise InvalidValue(
            f"assign_task_reviewer count must be in [1, 9] (got {count})")
    # 已指派的 reviewer 一律跳过（幂等：已开 N 票就不要再扩）。
    already_assigned = _assigned_task_reviewer_agent_ids(s, "task", task_id)
    if t.reviewer_id is not None and t.reviewer_agent_id is not None \
            and t.reviewer_agent_id not in already_assigned:
        # 历史遗留：reviewer_id 写过但 review_votes 还没记录
        # （单 review 模式从未建过 votes 行）。补一行 pending 占位。
        # 无 reviewer_agent_id 的旧行无法补票（计票身份缺失），跳过——
        # 这种 task 会继续走 single 模式的 reviewer_id 路径，不受影响。
        _insert_pending_vote(s, "task", task_id, t.reviewer_id, t.review_round or 0,
                             reviewer_agent_id=t.reviewer_agent_id)
        already_assigned.add(t.reviewer_agent_id)
    if len(already_assigned) >= count:
        # 多数决已被 N 票填满；不再追加
        s.commit()
        return t
    # 归属收敛（2026-09-01，决策 a/b）：评审也限 owner 名下的 agent。
    # - 候选池 = 在线可运行 ∩ Agent.user_id == task.created_by_user_id；
    # - 排除实现方 agent（get_assignment_exclusion("review") =
    #   same_task_implementer 的 agent_registry_ids）——替代旧的跨用户
    #   `user_id != assignee_id` 隔离（退休 reviewer isolation）；
    # - owner 为空 / 无第二个（非实现方）同 owner agent → **保持待处理**
    #   （in_review + reviewer 未指派，不 raise，决策 b），等 owner 再上线
    #   一个 agent；scan_review_timeouts 不会误扫 reviewer_id IS NULL 的行。
    owner_user_id = work_item_owner_user_id(t)
    if owner_user_id is None:
        log.info(
            "assign_task_reviewer: task %s 无 owner（owner_user_id=NULL），"
            "保持待处理（需人工补 owner）", task_id)
        s.commit()
        return t
    # T1.5：归属 + 自审排除走统一执行门。排除集 = 已派过票的 ∪ 动态排斥
    # （实现方 agent 不能评自己的活）。
    candidates = _online_reviewer_candidates(s, t.project_id)
    exclusion = get_assignment_exclusion(s, t, "review")
    exclude = set(already_assigned) | exclusion.agent_registry_ids
    candidates = [
        a for a in candidates if agent_can_handle_work_item(a, t, exclude_agent_ids=exclude)
    ]
    if not candidates:
        if not already_assigned:
            log.info(
                "assign_task_reviewer: task %s owner=%s 无非实现方在线 agent，"
                "保持待处理（决策 b）", task_id, owner_user_id)
        # 全无候选 / 部分已指派再无可补：不报错，保持当前状态。
        s.commit()
        return t
    ranked = rank_agents_for_task(s, t, role="reviewer", agents=candidates)
    if not ranked:
        log.info(
            "assign_task_reviewer: task %s owner=%s 候选均不满足能力要求，"
            "保持待处理", task_id, owner_user_id)
        s.commit()
        return t
    # 第一位走原有 CAS 把 reviewer_id 写进 Task（兼容旧查询 + 旧事件源），
    # 同时写 reviewer_agent_id（同 owner 多 agent 时路由 review 工作用）。
    # 后续走 review_votes 插入 pending 行，事件由 caller 自行 fan-out。
    # to_assign 元素 = (user_id, agent_registry_id)。
    # **一 agent 一票**（per-agent 计票，T1.1）：去重键是 Agent 而非 user。
    # 同一 owner 名下的多个 agent 各得一票，majority 模式才可能凑满 quorum；
    # 按 user 去重时同 owner 再多 agent 也只有 1 票，solo 部署永远过不了审。
    to_assign: list[tuple[int, int]] = []
    seen_agents: set[int] = set(already_assigned)
    for cand in ranked:
        aid = cand.agent.id
        if aid in seen_agents:
            continue
        if len(already_assigned) + len(to_assign) >= count:
            break
        to_assign.append((cand.agent.user_id, aid))
        seen_agents.add(aid)
    if t.reviewer_id is None and to_assign:
        first, first_agent = to_assign.pop(0)
        r = s.execute(
            update(Task).where(
                Task.id == task_id,
                Task.reviewer_id.is_(None),
                Task.status == Status.IN_REVIEW,
            ).values(reviewer_id=first, reviewer_agent_id=first_agent)
        )
        if r.rowcount == 1:
            _insert_pending_vote(s, "task", task_id, first, t.review_round or 0,
                                 reviewer_agent_id=first_agent)
        # rowcount != 1：并发写者抢先，把 first 当普通 pending 插入
        else:
            s.rollback()
            _insert_pending_vote(s, "task", task_id, first, t.review_round or 0,
                                 reviewer_agent_id=first_agent)
            already_assigned.add(first_agent)
    for uid, aid in to_assign:
        _insert_pending_vote(s, "task", task_id, uid, t.review_round or 0,
                             reviewer_agent_id=aid)
    _commit(s)
    s.refresh(t)
    return t


# ===================== PR-10: Implementation Task Dispatch =====================
# 替代 task.available 广播：Server 主动选 agent、状态机推 in_progress、publish
# task.assigned（4 字段齐全）→ .NET worker 接到才执行。
#
# PR-10 决策（用户 review 反馈）：
#   - 不用业务角色做 routing；workload 能力匹配后再解析物理 executor_type
#   - 当前简单随机（后期接 Agent.scores / 反馈做加权）
#   - "task 和 qa 不同 agent" 硬约束：从 TaskAssignment 历史查本 task
#     之前的 assignee，候选池里排除

# task_type + workload_type → agent_type（workbuddy/codex/minimax）
# PR-10 简单映射；后期接 tag/score 后这块改成 Agent.scores 查询
_DISPATCH_AGENT_TYPE = {
    ("design", "task"): "workbuddy",
    ("dev",    "task"): "codex",
    ("bug",    "task"): "codex",
    ("qa",     "task"): "workbuddy",
    # review 不走 dispatch（已有 assign_task_reviewer）
    # rework 跟 task 同 agent_type（修复用同一工具）
    ("dev",    "rework"): "codex",
    ("bug",    "rework"): "codex",
    ("design", "rework"): "workbuddy",
    ("qa",     "rework"): "workbuddy",
}


def _agent_type_for(task_type: str, workload_type: str) -> str | None:
    """PR-10：查 (task_type, workload_type) → agent_type。无映射返 None。"""
    return _DISPATCH_AGENT_TYPE.get((task_type, workload_type))


# PR-10 follow-up：物理 executor type 以 AgentInstance.executor_type 为准；
# roles 只在旧数据迁移期用于 CLI executor 兼容推导，不参与 workload 授权。
# .NET WorkflowMessageMapper 把 agent_type 必填，缺值 → DLQ。
_AGENT_EXECUTOR_TOOLS = ("codex", "workbuddy", "minimax", "qwen")
EXECUTOR_TYPES = frozenset((*_AGENT_EXECUTOR_TOOLS, "fake", "scenario"))


def resolve_agent_executor_type(agent, s: Session | None = None,
                                worker_id: str | None = None) -> str:
    """从 runnable AgentInstance 读取 executor type。

    ``roles`` 只作为旧数据兼容 fallback；新调度路径必须传 Session 并以
    AgentInstance.executor_type 为准。
    """
    if s is not None and agent is not None:
        q = s.query(AgentInstance).filter(
            AgentInstance.agent_id == agent.agent_id,
            AgentInstance.enabled.is_(True),
            AgentInstance.online.is_(True),
            AgentInstance.executor_type.isnot(None),
        )
        if worker_id:
            q = q.filter(AgentInstance.worker_id == worker_id)
        inst = q.order_by(AgentInstance.worker_id.asc()).first()
        if inst is not None and (inst.executor_type or "").strip():
            return str(inst.executor_type).strip().lower()
    import json as _json
    try:
        roles = _json.loads(agent.roles or "[]")
    except (ValueError, TypeError):
        roles = []
    for tool in _AGENT_EXECUTOR_TOOLS:
        if tool in roles:
            return tool
    return ""


def _excluded_prior_agent_ids(s: Session, task_id: int) -> set[int]:
    """PR-10："task 和 qa 不同 agent" 约束。

    查本 task 历史上所有 active/completed TaskAssignment 的 agent_registry_id
    （Agent.id int），dispatch 时从候选池排除。
    """
    rows = s.query(TaskAssignment.agent_registry_id).filter(
        TaskAssignment.task_id == task_id,
        TaskAssignment.status.in_(("active", "completed")),
        TaskAssignment.agent_registry_id.isnot(None),
    ).all()
    return {r[0] for r in rows if r[0] is not None}


@dataclass
class AssignmentExclusion:
    """一次 workload 的服务端排斥集合与可审计原因。"""

    agent_registry_ids: set[int] = field(default_factory=set)
    user_ids: set[int] = field(default_factory=set)
    reasons: dict[int, list[str]] = field(default_factory=dict)


def _assignment_rows_for_tasks(s: Session, task_ids: set[int]):
    if not task_ids:
        return []
    return s.query(TaskAssignment).filter(
        TaskAssignment.task_id.in_(task_ids),
        TaskAssignment.status.in_(("active", "completed")),
    ).all()


def _upstream_task_ids(s: Session, task_id: int) -> set[int]:
    """沿 TaskDependency.task_id → depends_on_id 取完整上游闭包。"""
    seen: set[int] = set()
    frontier = {task_id}
    while frontier:
        rows = s.query(TaskDependency.depends_on_id).filter(
            TaskDependency.task_id.in_(frontier),
        ).all()
        next_ids = {int(row[0]) for row in rows if row[0] is not None} - seen
        if not next_ids:
            break
        seen.update(next_ids)
        frontier = next_ids
    return seen


def get_assignment_exclusion(
    s: Session, task: Task, workload_type: str,
) -> AssignmentExclusion:
    """统一 review / QA 动态排斥策略。

    - review：排除当前 Task 的 active/completed implementer；
    - QA execution：排除上游依赖闭包中 Dev Task 的 implementer；
    - Design-only Agent 不在 QA 排斥集合中。
    """
    workload = (workload_type or "task").strip().lower()
    task_ids: set[int] = set()
    reason_label = ""
    if workload == "review":
        task_ids = {task.id}
        reason_label = f"same_task_implementer:task#{task.id}"
    elif str(task.type) == "qa" and workload in {"task", "rework", "qa"}:
        upstream = _upstream_task_ids(s, task.id)
        task_ids = {
            int(row[0])
            for row in s.query(Task.id).filter(
                Task.id.in_(upstream), Task.type == "dev",
            ).all()
        } if upstream else set()
        reason_label = "upstream_dev_implementer"

    result = AssignmentExclusion()
    for assignment in _assignment_rows_for_tasks(s, task_ids):
        if assignment.agent_registry_id is not None:
            aid = int(assignment.agent_registry_id)
            result.agent_registry_ids.add(aid)
            result.reasons.setdefault(aid, []).append(
                f"{reason_label}:task#{assignment.task_id}",
            )
        if assignment.user_id is not None:
            result.user_ids.add(int(assignment.user_id))
    return result


def _runnable_instance_for_agent(s: Session, agent: Agent) -> AgentInstance | None:
    return (
        s.query(AgentInstance)
        .join(Worker, Worker.worker_id == AgentInstance.worker_id)
        .filter(
            AgentInstance.agent_id == agent.agent_id,
            AgentInstance.enabled.is_(True),
            AgentInstance.online.is_(True),
            Worker.status == "active",
        )
        .order_by(AgentInstance.worker_id.asc())
        .first()
    )


def list_runnable_candidates(
    s: Session, task: Task, workload_type: str,
) -> list[tuple[Agent, AgentInstance]]:
    """统一 runnable Agent eligibility；不读取业务静态 roles。

    T1.5：归属判据走统一执行门 ``agent_can_handle_work_item`` —— 只看
    ``task.owner_user_id``，**不查 ProjectMember**。
    """
    expire_stale_agent_heartbeats(s)
    expire_stale_worker_heartbeats(s)
    owner_user_id = work_item_owner_user_id(task)
    # 归属收敛（2026-09-01，T1.5 收编）：只有 task owner 的 agent 才能入选，
    # 不再允许「任意项目成员的 agent」抢占别人的 task。owner 为空（存量/未标注）
    # 时 fail closed：返回空候选 → 派发保留 todo，等人工补 owner。
    if owner_user_id is None:
        log.warning(
            "dispatch: task %s 无 owner（owner_user_id=NULL），fail-closed 不派发",
            task.id,
        )
        return []
    online = s.query(Agent).filter(
        Agent.enabled.is_(True), Agent.online.is_(True),
    ).all()
    agents = [a for a in online if agent_can_handle_work_item(a, task)]
    # T4.1：ephemeral 模式下 DB 的 online 字段不再是唯一 presence 真源 ——
    # 与缓存在线状态求交（缓存由 WSS/HTTP HELLO/DELTA/PING 实时维护）。
    # DB 继续供 capability/roles 等静态属性；归属已在上面执行门判过，
    # 这里再对一次缓存的 user_id，双保险成本是一次内存遍历。
    if ephemeral_agents_enabled():
        cache = get_default_cache()
        agents = [
            a for a in agents
            if cache.has_online_agent(a.agent_id, user_id=owner_user_id)
        ]
    # 不变量「owner ∈ ProjectMember」由**写侧**保证（T1.4 回填 / T2.2 成员管理 /
    # 建项目时创建者自动入 owner）。这里只告警不再拦截：一旦写侧漏了，旧行为是
    # 静默返回空候选 → 整个项目派发停摆且无任何信号，排查成本极高；现在至少
    # 能在日志里看见是谁漏的。
    if agents:
        member_ids = {
            int(row[0]) for row in s.query(ProjectMember.user_id).filter(
                ProjectMember.project_id == task.project_id,
            ).all()
        }
        if owner_user_id not in member_ids:
            log.warning(
                "dispatch: task %s 的 owner user=%s 不是 project %s 的成员"
                "（写侧漏补 ProjectMember），派发仍继续",
                task.id, owner_user_id, task.project_id,
            )
    instances = {
        agent.id: _runnable_instance_for_agent(s, agent) for agent in agents
    }
    agents = [agent for agent in agents if instances.get(agent.id) is not None]
    ranked = rank_agents_for_task(
        s, task, role=workload_type or "task", agents=agents,
    )
    return [
        (entry.agent, instances[entry.agent.id])
        for entry in ranked
        if instances.get(entry.agent.id) is not None
    ]


def _online_agents_for_type(s: Session, tool: str) -> list[Agent]:
    """兼容 helper：按 executor type 查询，旧实例才回退到 roles 推导。

    这里的 roles fallback 只识别 Worker 使用的 CLI，不参与 workload 准入。
    """
    expected = (tool or "").strip().lower()
    agents = s.query(Agent).filter(
        Agent.online.is_(True), Agent.enabled.is_(True),
    ).all()
    return [
        agent for agent in agents
        if (inst := _runnable_instance_for_agent(s, agent)) is not None
        and resolve_agent_executor_type(agent, s=s, worker_id=inst.worker_id) == expected
    ]


def _instance_executor_type(agent: Agent, inst: AgentInstance, s: Session) -> str:
    """取 (Agent, AgentInstance) 的物理 executor type；instance 缺值时回退
    ``resolve_agent_executor_type``（roles 旧数据兼容）。"""
    et = (getattr(inst, "executor_type", "") or "").strip().lower()
    if et:
        return et
    return resolve_agent_executor_type(agent, s=s, worker_id=inst.worker_id)


def _pick_implementation_agent(
    s: Session, task: Task, workload_type: str,
) -> tuple[Agent, AgentInstance] | None:
    """按 workload 能力、在线实例和动态排斥规则选择实现者。

    返回 ``(Agent, AgentInstance)``；没有可运行候选或候选全部被排斥时
    返回 ``None``，由调用方保留 todo 并记录可观测的 deferred reason。

    P0-2 (2026-09-01)：在能力合格候选池内优先选择 ``_DISPATCH_AGENT_TYPE``
    映射的 preferred executor（design/qa→workbuddy、dev/bug→codex）。
    此前只按 capability score 排序 —— 任务没声明 needed_capabilities 时
    所有 agent coverage 都是 1.0，最终按 load/id 随机落位，Design 可能派给
    codex、Dev 可能派给 workbuddy。preferred 池为空时回退通用合格池
    （executor 离线时由其他 capable agent 兜底，不做硬性角色授权）。
    """
    if workload_type not in {"task", "rework"}:
        return None
    candidates = list_runnable_candidates(s, task, workload_type)
    if not candidates:
        return None
    excluded = get_assignment_exclusion(s, task, workload_type)
    filtered = [pair for pair in candidates if pair[0].id not in excluded.agent_registry_ids]
    if not filtered:
        log.warning(
            "dispatch: task %s 候选全被动态 exclusion 排除：%s",
            task.id, excluded.reasons,
        )
        return None
    preferred = _agent_type_for(str(task.type), workload_type)
    if preferred:
        preferred_pool = [
            pair for pair in filtered
            if _instance_executor_type(pair[0], pair[1], s) == preferred
        ]
        if preferred_pool:
            return preferred_pool[0]
        log.info(
            "dispatch: task %s (%s/%s) 无 executor_type=%s 的合格候选，回退通用池 %s",
            task.id, task.type, workload_type, preferred,
            [pair[0].agent_id for pair in filtered],
        )
    return filtered[0]


def dispatch_implementation_task(
    s: Session, task_id: int, *, workload_type: str = "task",
) -> tuple[Task, AgentInstance] | None:
    """PR-10：实现任务派发主入口。

    完整流程：
      1. 加载 task，验 status=todo（不能重复派发）
      2. 选 agent + worker（_pick_implementation_agent）
      3. 写 TaskAssignment（active_slot 唯一，PR-10 用 slot=1 占位）
      4. 状态机推 in_progress（用 set_status 走 TaskStateMachine）
      5. publish_workflow_event_for_agent(...)
         - agent_id=picked_agent.agent_id（logical）
         - worker_id=inst.worker_id（PR-5 resolve）
         - agent_type=picked_agent.agent_type（避免 .NET mapper 缺值 DLQ）
         - workload_type（PR-2 已支持）

    返 (task, instance) 或 None（无候选不阻塞，task 留 todo 等下次重试）。
    """
    task = s.get(Task, task_id)
    if task is None:
        raise NotFound(f"task {task_id} not found")
    if task.status != TaskStatus.TODO:
        log.info(
            "PR-10 dispatch: task %s status=%s，跳过（已派过）",
            task_id, task.status,
        )
        return None
    picked = _pick_implementation_agent(s, task, workload_type)
    if picked is None:
        runnable = list_runnable_candidates(s, task, workload_type)
        exclusion = get_assignment_exclusion(s, task, workload_type)
        remaining = [
            pair for pair in runnable
            if pair[0].id not in exclusion.agent_registry_ids
        ]
        # T1.5 验收②：scheduler 自动派发**不抛异常**（那是 agent 主动认领该得的
        # 403）。原因码要能区分三种情况，否则看板上「没人能干」和「没人认领」
        # 长得一样，排障全靠猜：
        #   no_owner              → 人工补 owner（T1.4 回填不了的那批）→ 保持 todo
        #   no_runnable_agent     → owner 名下无在线可运行 agent → blocked（T3.1）
        #   all_candidates_excluded → 有 agent 但被动态排斥（自审/并发槽位）→ blocked
        owner_user_id = work_item_owner_user_id(task)
        if owner_user_id is None:
            code = CODE_NO_OWNER
        elif runnable and not remaining:
            code = "all_candidates_excluded"
        else:
            code = "no_runnable_agent"
        task.assignment_deferred_reason = json.dumps({
            "code": code,
            "owner_user_id": owner_user_id,
            "task_type": task.type,
            "workload_type": workload_type,
            "runnable_agent_ids": [pair[0].id for pair in runnable],
            "excluded_agent_ids": sorted(exclusion.agent_registry_ids),
            "exclusion_reasons": exclusion.reasons,
        }, ensure_ascii=False, sort_keys=True)
        task.assignment_deferred_at = utc_now()
        _commit(s)
        log.warning("dispatch: task %s 无合格 Agent：%s",
                    task_id, task.assignment_deferred_reason)
        # T3.1：owner 存在但名下无可用 agent → 走状态机 todo→blocked，
        # 看板上能跟「排队中」区分开。no_owner 除外 —— 那是人工补 owner 的
        # 事，转 blocked 之后没有任何自动恢复路径（解锁钩子按 owner 找
        # agent），反而把待办藏起来。状态机 entry side-effect 会记
        # previous_status，T3.2 解锁按它恢复，不自定恢复目标（R10）。
        if code != CODE_NO_OWNER:
            set_status(
                s, task_id, str(TaskStatus.BLOCKED),
                reason=f"dispatch: {code}",
                status_reason=StatusReason.INSUFFICIENT_AGENTS.value,
            )
            log.warning(
                "dispatch: task %s 候选不足 → blocked（insufficient_agents）",
                task_id)
        return None
    agent, inst = picked
    # PR-10 follow-up：复用 try_assign_task 原子写 TaskAssignment + 推
    # status=in_progress + 设 assignee_id + current_assignment_id。
    # 之前手写的 4 步（add ta / flush / set current_assignment_id /
    # set_status）漏了 set task.assignee_id，导致 submit-review 校验挂
    # （"only the assignee can submit"），workbuddy/codex 干完活提交不进
    # in_review。try_assign_task 是单一 source of truth。
    from ..work_items.service import try_assign_task
    try:
        task, ta = try_assign_task(
            s,
            task_id,
            user_id=agent.user_id,
            agent_registry_id=agent.id,
            source="schedule",
            workload_type=workload_type,
            commit=True,
        )
    except Exception as e:
        # CAS 失败（被别的 emulator 抢先 claim）→ skip
        log.info("PR-10 dispatch: task %s try_assign_task 失败：%s，跳过",
                 task_id, e)
        return None
    # publish 4 字段齐全的 task.assigned（PR-5 helper 自动 resolve worker）
    # 注：PR-10 这里没传 agent_type —— Agent 模型无 agent_type 字段；
    # 改走 .NET 端按 agent_id 查注册的 tool（PR-12 启动注册时填到 worker 配置）
    # 本期 tool 类型（workbuddy/codex）从 _DISPATCH_AGENT_TYPE 反查
    # A targeted Worker can consume task.assigned before the request-level
    # transaction exits. Commit assignment/state before publishing work.
    s.commit()
    tool = resolve_agent_executor_type(agent, s=s, worker_id=inst.worker_id)
    from ...core.infrastructure import messaging as mq
    mq.publish_workflow_event(
        mq.EVENT_TASK_ASSIGNED,
        "task",
        task_id,
        ref_id=task.story_id,
        agent_id=agent.agent_id,  # 逻辑身份（body 留 trace）
        worker_id=inst.worker_id,  # PR-5：物理身份 → routing key
        agent_type=tool,  # 关键：.NET mapper 必填，缺值 → DLQ
        workload_type=workload_type,
        # P0-2（2026-09-01 review）：task type 进 body，
        # .NET prompt builder 按 design/dev/qa 分执行语义
        task_type=str(task.type or "") or None,
    )
    log.info(
        "PR-10 dispatch: task %s → agent_id=%s agent_type=%s worker_id=%s "
        "(%s)",
        task_id, agent.agent_id, tool, inst.worker_id, workload_type,
    )
    return (task, inst)


def _assigned_task_reviewer_agent_ids(s: Session, entity_type: str,
                                      entity_id: int) -> set[int]:
    """返回该实体上所有已建 review_votes 行的 reviewer agent_id（含 pending NULL）。

    计票身份是 Agent（per-agent 一票），因此"已指派"判定也必须按 agent 维度——
    按 user 判定会让同 owner 的第二个 agent 被视为已投过票而拿不到票，
    正是 solo 多 agent 永远凑不满 quorum 的根因。

    比单看 ``Task.reviewer_id`` 更准确：多 review 模式下 review_votes
    是事实源（reviewer_id 仅首位）。
    """
    from ...features.projects.models import ReviewVote  # 局部 import 避免循环
    rows = s.query(ReviewVote.reviewer_agent_id).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).all()
    return {int(r[0]) for r in rows if r[0] is not None}


def _insert_pending_vote(s: Session, entity_type: str, entity_id: int,
                         reviewer_user_id: int, round_: int,
                         reviewer_agent_id: int) -> None:
    """插入一条 pending (NULL verdict) review_votes 行。已存在则跳过（幂等）。

    ``reviewer_agent_id`` 必填：它是唯一性判定的一部分（UNIQUE 含该列），
    留空会让待投票变成"无主票"，既无法路由也无法计票。
    """
    from ...features.projects.models import ReviewVote
    existing = s.query(ReviewVote.id).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
        ReviewVote.reviewer_agent_id == reviewer_agent_id,
    ).first()
    if existing is not None:
        return
    s.add(ReviewVote(
        entity_type=entity_type,
        entity_id=entity_id,
        reviewer_user_id=reviewer_user_id,
        reviewer_agent_id=reviewer_agent_id,
        verdict=None,
        round=round_,
    ))


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


def _reviewer_comment_author(
    s: Session,
    reviewer_user_id: int,
    *,
    explicit_name: str | None = None,
    reviewer_agent_id: int | None = None,
) -> str:
    """Resolve the stable Agent name used for an automatic review comment.

    计票身份下沉到 agent 后（T1.1），无名回退路径也要按 **投票 agent** 定名，
    不能按 user 反查首个 agent —— 同 owner 多 agent 时那会把票记到别人名下。
    """
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()[:100]
    if reviewer_agent_id is not None:
        voting = s.get(Agent, reviewer_agent_id)
        if voting is not None:
            return (voting.name or voting.agent_id
                    or f"user#{reviewer_user_id}").strip()[:100]
    agent = (
        s.query(Agent)
        .filter(Agent.user_id == reviewer_user_id, Agent.enabled.is_(True))
        .order_by(Agent.online.desc(), Agent.id.desc())
        .first()
    )
    if agent is not None:
        return (agent.name or agent.agent_id or f"user#{reviewer_user_id}").strip()[:100]
    reviewer = s.get(User, reviewer_user_id)
    return (reviewer.display_name or reviewer.username if reviewer else f"user#{reviewer_user_id}")[:100]


def review_task(s: Session, *, task_id: int, reviewer_user_id: int,
                verdict: str, comment: str,
                reviewer_agent_id: int | None = None,
                reviewer_agent_name: str | None = None) -> Task:
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
            reviewer_agent_id=reviewer_agent_id,
            verdict=verdict, comment=comment,
            reviewer_agent_name=reviewer_agent_name)
        return t
    if t.reviewer_id != reviewer_user_id:
        raise InvalidValue("only the assigned reviewer can review this task")
    if t.status != Status.IN_REVIEW:
        raise InvalidValue(f"task is not in_review (current status: {t.status})")
    reviewer_name = _reviewer_comment_author(
        s, reviewer_user_id, explicit_name=reviewer_agent_name,
    )

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

    T3.2：agent 从 offline → online 的那次心跳会触发解锁钩子，重扫该 owner
    名下 blocked(insufficient_agents) 的队列。只在翻转时触发——心跳是高频
    调用，每次都扫全队列是纯浪费。
    """
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    if user_id is not None and agent.user_id not in (None, user_id):
        raise InvalidValue("heartbeat rejected: agent belongs to another user")
    was_online = bool(agent.online)
    agent.online = True if probe_ok is None else probe_ok
    agent.last_heartbeat = utc_now()
    if probe_message:
        agent.probe_message = str(probe_message)[:300]
        agent.last_probe_at = utc_now()
    if user_id is not None and agent.user_id is None:
        agent.user_id = user_id
    _commit(s); s.refresh(agent)
    # T3.2 解锁钩子：独立 session 上下文之外最好别做重活，这里在翻转时才扫，
    # 且 unblock 内部自限（无候选就不动）。user_id 缺失时退回 agent.user_id。
    if not was_online and agent.online:
        owner = agent.user_id or user_id
        if owner is not None:
            try:
                unblocked = unblock_insufficient_agent_tasks(s, owner)
                if unblocked:
                    log.info("agent_heartbeat: agent %s 上线，解锁 %s 个 "
                             "insufficient_agents 任务", agent_id, unblocked)
            except Exception:  # 解锁失败不阻断心跳保活
                log.exception("agent_heartbeat: 解锁钩子失败（agent=%s）",
                              agent_id)
    return agent


# ---- T3.2 解锁钩子 ------------------------------------------------------------

def unblock_insufficient_agent_tasks(s: Session, owner_user_id: int) -> int:
    """重扫某 owner 名下 blocked(insufficient_agents) 的队列，能跑的解锁。

    T3.2：agent 上线/心跳恢复时调用。解锁目标**不自定**（R10）—— 按状态机
    进入 blocked 时记录的 ``previous_status`` 恢复（todo/in_progress/in_review/
    done 四个迁移都已注册）；previous_status 为空的存量数据退回 todo。

    「能不能跑」复用派发逻辑 ``_pick_implementation_agent`` 判定 —— 它包含
    在线/enabled/runnable instance/capability/动态排斥全套，与派发口径一致；
    单独再写一套「agent 是否可用」判断必然漂移。

    只恢复状态、清 deferred reason；派发由调度器下一轮正常进行
    （dispatch 成功会写 assignment 并清 deferred reason）。
    返回解锁数量。
    """
    from ..work_items.models import Task as TaskModel
    from ..work_items.state_machine import TaskStateMachine

    blocked_tasks = (
        s.query(TaskModel)
        .filter(
            TaskModel.status == Status.BLOCKED,
            TaskModel.status_reason == StatusReason.INSUFFICIENT_AGENTS.value,
            TaskModel.owner_user_id == owner_user_id,
        )
        .order_by(TaskModel.id.asc())
        .all()
    )
    if not blocked_tasks:
        return 0
    sm = TaskStateMachine()
    unblocked = 0
    for task in blocked_tasks:
        # 与派发同一把尺子：有合格候选才解锁，不为凑数放行
        if _pick_implementation_agent(s, task, "task") is None:
            continue
        target = task.previous_status or TaskStatus.TODO
        try:
            sm.execute(s, task, str(target),
                       ctx={"reason": "unblock: agent online"})
        except Exception:
            # previous_status 可能指向已不合法的迁移（脏数据）——退回 todo 再试
            log.warning("unblock: task %s 恢复 %s 失败，退回 todo",
                        task.id, target, exc_info=True)
            try:
                sm.execute(s, task, str(TaskStatus.TODO),
                           ctx={"reason": "unblock: agent online (fallback)"})
            except Exception:
                log.exception("unblock: task %s 恢复 todo 也失败，跳过", task.id)
                continue
        task.assignment_deferred_reason = None
        task.assignment_deferred_at = None
        unblocked += 1
    if unblocked:
        _commit(s)
        log.info("unblock: owner=%s 解锁 %s 个任务", owner_user_id, unblocked)
    return unblocked


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
        # T3.2 解锁钩子：实例心跳让 agent 恢复在线时同样触发
        # （这是 Worker 场景下 agent「上线」的真实路径，agent_heartbeat 只覆盖
        #  自报心跳）。失败不阻断在线聚合。
        if agent.online and agent.user_id is not None:
            try:
                unblock_insufficient_agent_tasks(s, agent.user_id)
            except Exception:
                log.exception("_sync_agent_online: 解锁钩子失败（agent=%s）",
                              agent_id)


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
    # 读前先 reconcile stale worker（PR-1 review 收尾：happy path 之前
    # 必须保证 Worker.status 反映当前真实存活情况）
    expire_stale_worker_heartbeats(s)
    return s.query(Worker).filter(Worker.worker_id == worker_id).first()


def list_workers(s: Session) -> list[Worker]:
    # 同 get_worker_by_id
    expire_stale_worker_heartbeats(s)
    return s.query(Worker).order_by(Worker.id.asc()).all()


def resolve_worker_for_agent(s: Session, agent_id: str) -> str | None:
    """PR-5：给定逻辑 agent_id，找一个能执行它的 worker_id。

    用途：FastAPI publish 路径要把 ``agent_id``（逻辑身份）翻成
    ``worker_id``（物理身份）才能正确投递到 ``workflow.agent.{workerId}``
    队列（.NET 端按 ``_identity.WorkerId`` 订阅，见 WorkflowMqConsumerService）。

    找法（顺序）：
      1. 任何 ``enabled=True`` + ``online=True`` 的 ``AgentInstance`` 中
         ``agent_id`` 匹配的，取 ``worker_id``
      2. 没匹配 → 返回 None（caller 决定 fallback；典型是 broadcast 让
         任何在线 worker 抢）

    多 worker 部署时可能返回多个候选；目前取 worker_id 升序第一个
    （稳定可预测）。后续要加 "选最近心跳 / 选最低负载" 可在此扩展。
    """
    if not agent_id:
        return None
    row = (
        s.query(AgentInstance.worker_id)
        .filter(
            AgentInstance.agent_id == agent_id,
            AgentInstance.enabled.is_(True),
            AgentInstance.online.is_(True),
        )
        .order_by(AgentInstance.worker_id.asc())
        .first()
    )
    return row[0] if row else None


def publish_workflow_event_for_agent(
    s: Session,
    event: str,
    entity_type: str,
    entity_id: int,
    *,
    agent_id: str | None,
    ref_id: int | None = None,
    **kwargs,
) -> bool:
    """PR-5：把 ``publish_workflow_event`` 的 agent_id 路径升级成 worker_id 优先。

    流程：
      1. 用 ``resolve_worker_for_agent`` 把 agent_id 翻成 worker_id
      2. publish 时同时传 agent_id（body 审计用）和 worker_id（routing 用）；
         publisher 优先用 worker_id 当 routing key
      3. resolve 失败 → agent_id 仍照传（向后兼容老 broken 行为，
         log warning 让运维知道没在线 worker 可发）

    kwargs 透传给 ``publish_workflow_event``：``agent_type`` /
    ``workload_type`` / ``correlation_id`` / ``route`` 等。
    """
    # 延迟 import 避免循环（mq 模块要 scheduling.models）
    from ...core.infrastructure import messaging as mq
    worker_id = resolve_worker_for_agent(s, agent_id) if agent_id else None
    if agent_id and not (kwargs.get("agent_type") or "").strip():
        inst = s.query(AgentInstance).filter(
            AgentInstance.agent_id == agent_id,
            AgentInstance.worker_id == worker_id,
            AgentInstance.enabled.is_(True),
            AgentInstance.online.is_(True),
        ).first() if worker_id else None
        if inst is not None:
            agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
            kwargs["agent_type"] = resolve_agent_executor_type(
                agent, s=s, worker_id=inst.worker_id,
            ) if agent is not None else ""
    if agent_id and not worker_id:
        # 运维可见：task / review 发出去了但没有在线 worker
        # .NET 端走 fallback（agent_id 路由，几乎没人收；happy path
        # 应保证 Worker 表有 AgentInstance + heartbeat 在线）
        mq.log.warning(
            "publish_workflow_event_for_agent: agent_id=%r 没有在线 worker（"
            "AgentInstance.enabled+online 全空），回退 agent_id 路由（"
            "通常 .NET 收不到，需要补 Worker / AgentInstance 配置）",
            agent_id,
        )
    return mq.publish_workflow_event(
        event, entity_type, entity_id,
        ref_id=ref_id,
        agent_id=agent_id,
        worker_id=worker_id,
        **kwargs,
    )


def upsert_agent_instance(
    s: Session, *,
    worker_id: str,
    agent_id: str,
    cli_command: str = "",
    model: str = "",
    executor_type: str | None = None,
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
    normalized_executor = (
        str(executor_type).strip().lower() if executor_type is not None else None
    )
    if normalized_executor == "":
        normalized_executor = None
    if normalized_executor is not None and normalized_executor not in EXECUTOR_TYPES:
        raise InvalidValue(
            f"executor_type must be one of {sorted(EXECUTOR_TYPES)}",
        )
    existing = s.query(AgentInstance).filter(
        AgentInstance.worker_id == worker_id,
        AgentInstance.agent_id == agent_id,
    ).first()
    if existing:
        existing.cli_command = (cli_command or "")[:500]
        existing.model = (model or "")[:100]
        # Older Workers omit executor_type; an idempotent upsert must not erase
        # a value already registered by a newer Worker.
        if executor_type is not None:
            existing.executor_type = normalized_executor
        existing.auth_key = (auth_key or "")[:100]
        existing.enabled = bool(enabled)
        _commit(s); s.refresh(existing); return existing
    inst = AgentInstance(
        worker_id=worker_id,
        agent_id=agent_id,
        cli_command=(cli_command or "")[:500],
        model=(model or "")[:100],
        executor_type=normalized_executor,
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

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def get_review_mode() -> str:
    """评审模式：环境变量 AGENTBOARD_REVIEW_MODE（single|majority），非法回退 single。"""
    mode = os.environ.get("AGENTBOARD_REVIEW_MODE", "").strip().lower()
    return mode if mode in (REVIEW_MODE_SINGLE, REVIEW_MODE_MAJORITY) else REVIEW_MODE_SINGLE

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def get_review_quorum() -> int:
    """法定票数：AGENTBOARD_REVIEW_QUORUM（1..9），非法/缺省回退 DEFAULT_REVIEW_QUORUM。

    T1.2（2026-09-02）：默认值 3 → 1。归属收敛后计票实体是「能投票的
    agent」，单成员部署下这个基数很小，quorum=3 会让 majority 模式永远
    凑不满票、任务卡在 in_review 直到超时被扫走。默认 1 = 首票即结算，
    需要更强评审时用环境变量上调。

    ⚠ 常量真源在 scheduling/models.py，本文件不再重复定义。生产环境如果
    显式设了 AGENTBOARD_REVIEW_QUORUM，改常量**不会**生效，必须同步改 env。
    """
    raw = os.environ.get("AGENTBOARD_REVIEW_QUORUM", "").strip()
    try:
        q = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_QUORUM
    return q if 1 <= q <= 9 else DEFAULT_REVIEW_QUORUM

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _is_reviewer_candidate(s: Session, project_id: int, agent_id: int,
                           exclude_agent_ids: set[int] | None = None) -> bool:
    """投票 Agent 校验（majority 模式）：在线可运行 ∩ 项目成员 ∩ ∉exclude。

    **计票身份是 Agent**（per-agent 一票，T1.1）：参数由旧的 ``user_id`` 改为
    ``agent_id``。单成员多 worker 下同一 user 挂多个 agent，按 user 校验无法
    区分「谁投的票」，也无法阻止同一 agent 重复投票。

    与分配器候选集同源（``_online_reviewer_candidates``），保证只有能被指派为
    reviewer 的 Agent 才能参与多数决投票（评审强度升级，但参与者资格不变）。

    注：角色（``roles``）不参与准入判定 —— 见 ``_online_reviewer_candidates``
    与 workload 准入相关注释（roles 仅用于旧数据 CLI executor 推导）。
    """
    if agent_id is None:
        return False
    if exclude_agent_ids and agent_id in exclude_agent_ids:
        return False
    for a in _online_reviewer_candidates(s, project_id):
        if a.id == agent_id:
            return True
    return False

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _upsert_review_vote(s: Session, *, entity_type: str, entity_id: int,
                        reviewer_user_id: int, reviewer_agent_id: int,
                        verdict: str,
                        comment_id: int | None, round: int) -> None:
    """一 agent 一票 upsert：存在则更新 verdict/comment（改票），否则插入。

    唯一性键是 ``reviewer_agent_id``（per-agent 计票）——同一 owner 名下不同
    agent 各持一票，同一 agent 重复投票只改最后一票。
    ``reviewer_user_id`` 只作归属/审计记录，不参与查重。

    双后端兼容：先查后写（量级小，避免方言差异的 ON CONFLICT 语法）。
    """
    existing = s.query(ReviewVote).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
        ReviewVote.reviewer_agent_id == reviewer_agent_id,
    ).first()
    if existing is not None:
        existing.verdict = verdict
        existing.comment_id = comment_id
        existing.round = round
        _commit(s)
        return
    s.add(ReviewVote(entity_type=entity_type, entity_id=entity_id,
                     reviewer_user_id=reviewer_user_id,
                     reviewer_agent_id=reviewer_agent_id,
                     verdict=verdict,
                     comment_id=comment_id, round=round))
    _commit(s)

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _review_vote_counts(s: Session, entity_type: str, entity_id: int) -> tuple[int, int]:
    """返回 (approve, reject) 票数。"""
    rows = s.query(ReviewVote.verdict, func.count(ReviewVote.id)).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).group_by(ReviewVote.verdict).all()
    counts = dict(rows)
    return int(counts.get("approve", 0)), int(counts.get("reject", 0))

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _clear_review_votes(s: Session, entity_type: str, entity_id: int) -> None:
    """结算后清票（终态 / 驳回后开新一轮，MVP 简化：历史票不跨轮保留）。"""
    s.query(ReviewVote).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).delete(synchronize_session=False)
    _commit(s)

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _settle_majority_approved(s: Session, entity, entity_type: str):
    """多数通过（CAS）：Task in_review→done，结算后清票。

    Story 分支已移除（2026-09-02 T0.1c）：Story 级评审自 2026-08-09 起下线
    （``assign_reviewer`` 直接抛 ``InvalidValue``），Story 不会再进入
    ``pending_review`` + 已指派 reviewer 的组合，故该分支为死路径。
    """
    if entity_type != "task":
        raise InvalidValue(
            f"majority settle: unsupported entity_type '{entity_type}' "
            "(Story review has been retired; only 'task' is supported)"
        )
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

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _settle_majority_rejected(s: Session, entity, entity_type: str):
    """多数驳回：review_round+1，Task 回 in_progress；
    达 MAX_REVIEW_ROUNDS → blocked 护栏；结算后清票（下一轮重新投票）。

    Story 分支已移除（2026-09-02 T0.1c）：Story 级评审已下线，该分支为死路径。
    """
    if entity_type != "task":
        raise InvalidValue(
            f"majority settle: unsupported entity_type '{entity_type}' "
            "(Story review has been retired; only 'task' is supported)"
        )
    new_round = (entity.review_round or 0) + 1
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

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def resolve_reviewer_agent_id(s: Session, reviewer_user_id: int,
                              explicit_agent_id: int | None = None) -> int | None:
    """解析投票人（计票身份）的 agent id：显式优先，唯一 agent 兜底，歧义 None。

    T1.1 后计票单位是 agent 而非 user，但调用链上并非每层都拿得到 agent
    身份：

    - **Router 层**（有 Authorization）：``resolve_actor_context`` 能从 API key
      拿到权威 ``agent_registry_id``，必须显式传入；
    - **服务层直调**（人类登录 / 旧 key / 内部脚本）：拿不到就只能从
      ``user_id`` 反推，且**仅当该 user 名下恰好一个 enabled agent** 才可推断。

    多 agent（含 0 个）时返回 ``None`` 让调用方 fail closed —— 「谁投的票」
    不可判定时猜一个，比拒绝更糟：它会把票记到别人名下，审计全错。
    """
    if explicit_agent_id is not None:
        return int(explicit_agent_id)
    owned = s.query(Agent.id).filter(
        Agent.user_id == reviewer_user_id,
        Agent.enabled.is_(True),
    ).all()
    if len(owned) == 1:
        return int(owned[0].id)
    return None


# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _vote_majority(s: Session, entity, *, entity_type: str, reviewer_user_id: int,
                   reviewer_agent_id: int | None,
                   verdict: str, comment: str,
                   reviewer_agent_name: str | None = None):
    """多数决投票（S3 M3）：写票（一 agent 一票 upsert）→ 达法定票数结算。

    - **计票身份是 Agent**（per-agent 一票，T1.1）：``reviewer_agent_id`` 是必填
      项，缺失直接 ``InvalidValue``（fail closed）——没有 agent 身份就写票，等于
      退回「同 owner 多 agent 只有 1 票、solo 部署永远过不了审」的老问题；
    - 权限：投票 Agent 须是该项目在线 reviewer 候选（与分配器同源）；
      Task 版排除**实现方 agent**（评审人与作者隔离，agent 粒度）；
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
        exclude: set[int] = set()
    else:
        project_id = entity.project_id
        expected_status = Status.IN_REVIEW
        # 归属收敛（2026-09-01）：Task 投票人必须是 owner（created_by_user_id）
        # 本人——替代旧的「排除 assignee user」跨用户隔离（reviewer 与实现方
        # 同 owner 后，按 user 排除会误杀唯一合法投票人）。
        if entity.created_by_user_id is not None:
            if reviewer_user_id != entity.created_by_user_id:
                raise InvalidValue(
                    "only the task owner's agent can vote on this task (majority mode)")
        # 实现方隔离下沉到 agent 粒度（T1.1）：与指派侧
        # （assign_task_reviewer → get_assignment_exclusion("review")）同源，
        # 两个来源取并集——
        #   1. TaskAssignment 里 active/completed 的实现 agent（可能被改派过，
        #      比 created_by_agent_id 更能反映真实实现方）；
        #   2. task.created_by_agent_id（任务创建者，无 assignment 时的兜底）。
        # 只有按 agent 排除才能真正做到「不能评审自己的实现」。
        exclude = set(
            get_assignment_exclusion(s, entity, "review").agent_registry_ids)
        if entity.created_by_agent_id is not None:
            exclude.add(int(entity.created_by_agent_id))
    reviewer_agent_id = resolve_reviewer_agent_id(
        s, reviewer_user_id, explicit_agent_id=reviewer_agent_id)
    if reviewer_agent_id is None:
        raise InvalidValue(
            "reviewer_agent_id is required (majority mode votes per agent); "
            f"user#{reviewer_user_id} has no unambiguous enabled agent")
    if reviewer_agent_id in exclude:
        raise InvalidValue(
            "the implementing agent cannot review its own task (majority mode)")
    if not _is_reviewer_candidate(s, project_id, reviewer_agent_id, exclude):
        raise InvalidValue(
            "only an online reviewer agent of this project can vote (majority mode)")
    if entity.status != expected_status:
        raise InvalidValue(
            f"entity is not {expected_status} (current status: {entity.status})")
    reviewer_name = _reviewer_comment_author(
        s, reviewer_user_id, explicit_name=reviewer_agent_name,
        reviewer_agent_id=reviewer_agent_id,
    )
    # 评审意见落评论（唯一载体，与 single 模式一致）
    comment_obj = create_comment(
        s, author=reviewer_name, content=comment,
        **({f"{entity_type}_id": entity.id}))
    _upsert_review_vote(s, entity_type=entity_type, entity_id=entity.id,
                        reviewer_user_id=reviewer_user_id,
                        reviewer_agent_id=reviewer_agent_id,
                        verdict=verdict,
                        comment_id=comment_obj.id, round=entity.review_round or 0)
    approve_n, reject_n = _review_vote_counts(s, entity_type, entity.id)
    if approve_n + reject_n < get_review_quorum():
        s.refresh(entity)
        return entity, False
    if approve_n > reject_n:
        return _settle_majority_approved(s, entity, entity_type), True
    return _settle_majority_rejected(s, entity, entity_type), True

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _online_reviewer_candidates(s: Session, project_id: int) -> list[Agent]:
    """在线、enabled、项目成员且有 runnable instance 的通用评审候选。"""
    expire_stale_agent_heartbeats(s)
    expire_stale_worker_heartbeats(s)
    member_ids = {
        r[0] for r in s.query(ProjectMember.user_id).filter(
            ProjectMember.project_id == project_id
        ).all()
    }
    online_agents = s.query(Agent).filter(
        Agent.online.is_(True), Agent.enabled.is_(True),
    ).all()
    candidates = []
    for a in online_agents:
        if a.user_id not in member_ids:
            continue
        if _runnable_instance_for_agent(s, a) is not None:
            candidates.append(a)
    return candidates

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def assign_reviewer(s: Session, story_id: int, *, user_id: int | None = None,
                    is_admin: bool = False) -> Story:
    """Story 级评审已下线（Ticket 全流程，2026-08-09）。

    评审职责整体下沉 Task 层（design task 的 in_design 评审流 / 实现 task 的
    in_review 评审）。调用方应改用 Task 的 ``assign_task_reviewer``。
    """
    raise InvalidValue("Story 评审已下线：评审在 Task 层进行（design 评审 / 实现评审）")

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def list_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的评审任务（Story，按 pending_review 优先排序）。"""
    q = s.query(Story).filter(Story.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES and status not in STORY_REVIEW_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Story.status == status)
    q = q.order_by(Story.status.desc(), Story.id.desc())
    return q.all()

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def list_task_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的 Task 评审任务（按 in_review 优先排序）。"""
    q = s.query(Task).filter(Task.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Task.status == status)
    q = q.order_by(Task.status.desc(), Task.id.desc())
    return q.all()

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _reassign_story_reviewer(s: Session, story: Story,
                             exclude_user_id: int | None = None) -> int | None:
    """Story 超时重派：候选排除旧 reviewer，CAS（pending_review AND reviewer_id IS NULL）。

    调用前 reviewer 必须已解绑（CAS 由调用方仲裁）；候选为空 → None（保持解绑，
    由下轮轮询补派，评审流不因重派失败而卡死）。成功返回新 reviewer 的 user_id。

    T1.5：补 owner 门 —— 这里是全链路**唯一**没有归属过滤的重派入口，别的
    agent 可以评到别人 Story 上。判据改走统一执行门（owner_user_id）。
    旧的 `a.user_id != exclude_user_id` 只是「别再派给刚超时的那个 user」，
    跟归属无关，保留。
    """
    epic = s.get(Epic, story.epic_id)
    if epic is None:
        return None
    if work_item_owner_user_id(story) is None:
        return None  # owner 为空 fail-closed
    candidates = _online_reviewer_candidates(s, epic.project_id)
    candidates = [
        a for a in candidates
        if a.user_id != exclude_user_id and agent_can_handle_work_item(a, story)
    ]
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

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
def _reassign_task_reviewer(s: Session, task: Task,
                            exclude_user_id: int | None = None) -> int | None:
    """Task 超时重派（归属收敛版，与 core facade 副本同步）：
    候选 = owner 名下在线 agent，排除旧评审 agent（reviewer_agent_id）
    与实现方 agent（assignment exclusion / current_assignment），CAS 写
    reviewer_id + reviewer_agent_id。无候选 → None（保持待处理，决策 b）。

    旧的「排除 assignee user」跨用户隔离已退休（同 owner 评审）。
    """
    # T1.5：归属 + 自审排除走统一执行门（判 owner_user_id，不是 created_by）。
    if work_item_owner_user_id(task) is None:
        return None  # owner 为空 fail-closed，保持待处理
    candidates = _online_reviewer_candidates(s, task.project_id)
    exclusion = get_assignment_exclusion(s, task, "review")
    exclude = exclusion.agent_registry_ids | {task.reviewer_agent_id}
    candidates = [
        a for a in candidates
        if agent_can_handle_work_item(a, task, exclude_agent_ids=exclude)
    ]
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
        ).values(reviewer_id=reviewer.user_id, reviewer_agent_id=reviewer.id)
    )
    if r.rowcount != 1:
        s.rollback()
        return None
    _commit(s)
    return reviewer.user_id

# ---- 真源（2026-09-02 收敛：core 侧重复实现已删除，core 末尾统一转发自此）----
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
