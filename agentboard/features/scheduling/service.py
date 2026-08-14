"""Scheduling service:Schedule / Run / Agent / Review。

Phase 4 第五段:从 service.py 拆分。本文件仅作 facade 装载新模块;老 import
路径由 service.py 末尾 ``from .features.X.service import *`` 重绑保持兼容。

本文件不实现业务逻辑,只是把 service.py 里同主题的函数搬家过来 + 加必要的
import,行为完全一致。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容
from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
)
from ...core.service_helpers import (
    _commit, _invalidate_project_stats_cache, _paginate, _required,
)

log = logging.getLogger("agentboard.features.scheduling.service")

# 评审超时(30 分钟,任务超过这个时间还没人评审就重新指派)
DEFAULT_REVIEW_TIMEOUT_MINUTES = 30
DEFAULT_TIMEOUT_SCAN_BATCH = 20
MAX_REVIEW_ROUNDS = 5
DEFAULT_REVIEW_QUORUM = 3

# Agent 在 projects.models
from ..projects.models import Project
from ..work_items.models import Task
from .models import AgentRun, AgentSchedule

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
    run = AgentRun(
        schedule_id=schedule_id, task_id=task_id,
        agent=agent_id, model=agent_config.model if agent_config else None,
        idempotency_key=idempotency_key,
    )
    s.add(run); _commit(s); s.refresh(run); return run



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
        .values(status="confirmed")
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
                   capabilities: str = "[]", cli_command: str = "",
                   model: str = "", auth_key: str = "", user_id: int | None = None) -> Agent:
    """注册/更新 Agent（幂等：agent_id 已存在则更新字段）。

    agent_id 为外部 Agent 自报唯一标识；roles/capabilities 为 JSON 数组串。
    cli_command 支持 ``{model}`` 占位符（同一 CLI 多 agent 各自注入模型）。
    user_id 绑定服务账号用户（经 ProjectMember 授权参与项目协作）。
    """
    agent_id = _required(agent_id, "agent_id", 64)
    name = _required(name, "name", 100)
    roles_list = _parse_json_list(roles, "roles")
    caps_list = _parse_json_list(capabilities, "capabilities")
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
            r = s.execute(update(Task).where(
                Task.id == t.id,
                Task.status == Status.IN_REVIEW,
            ).values(status=Status.BLOCKED))
            if r.rowcount == 1:
                _record_status_history(s, t.id, str(Status.IN_REVIEW), str(Status.BLOCKED),
                                       reason="timeout max review rounds")
                _commit(s)
                result["blocked"] += 1
            else:
                s.rollback()
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



def list_agents(s: Session, *, online: bool | None = None, role: str | None = None):
    q = s.query(Agent)
    if online is not None:
        q = q.filter(Agent.online == online)
    if role:
        rows = q.order_by(Agent.id.desc()).all()
        return [a for a in rows if role in _parse_json_list(a.roles, "roles")]
    return q.order_by(Agent.id.desc()).all()


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
    reviewer = random.choice(candidates)
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



def claim_story(s: Session, id: int, *, changed_by: int | None = None) -> Story:
    """Worker 竞争认领 Story（Ticket 全流程多实例编排）：CAS confirmed → todo。

    多个 Worker 实例（不同 agent CLI）同时扫描同一 confirmed Story 时，
    条件 UPDATE ``status=confirmed`` → ``todo``，rowcount=1 恰一赢家；
    竞争失败抛 IllegalTransition（api 层转 409）。todo 语义 = 已被某 worker
    认领处理中（其它实例扫描 confirmed 不再看到），失败/交接由
    ``unclaim_story`` 回退 confirmed 重新入池。
    """
    st = s.get(Story, id)
    if not st:
        raise NotFound(f"story {id} not found")
    r = s.execute(
        update(Story).where(Story.id == id, Story.status == "confirmed")
        .values(status="todo")
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

    评论是评审意见唯一载体（approve/reject 必须伴随 comment），形成审计轨迹。
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

    if verdict == "approve":
        r = s.execute(
            update(Task).where(
                Task.id == task_id,
                Task.reviewer_id == reviewer_user_id,
                Task.status == Status.IN_REVIEW,
            ).values(status=Status.DONE)
        )
        if r.rowcount != 1:
            s.rollback()
            raise InvalidValue("review conflict: task state changed concurrently")
        _record_status_history(s, task_id, str(Status.IN_REVIEW), str(Status.DONE),
                               changed_by=reviewer_user_id, reason="review approve")
        _commit(s)
    else:  # reject
        new_round = (t.review_round or 0) + 1
        target = Status.BLOCKED if new_round >= MAX_REVIEW_ROUNDS else Status.IN_PROGRESS
        r = s.execute(
            update(Task).where(
                Task.id == task_id,
                Task.reviewer_id == reviewer_user_id,
                Task.status == Status.IN_REVIEW,
            ).values(review_round=new_round, status=target)
        )
        if r.rowcount != 1:
            s.rollback()
            raise InvalidValue("review conflict: task state changed concurrently")
        _record_status_history(s, task_id, str(Status.IN_REVIEW), str(target),
                               changed_by=reviewer_user_id,
                               reason=f"review reject round={new_round}")
        _commit(s)
    # 评审意见落评论（唯一载体）
    create_comment(s, author=reviewer_name, content=comment, task_id=task_id)
    _invalidate_project_stats_cache(t.project_id)
    s.refresh(t)
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



def claim_development_task(s: Session, task_id: int, *, user_id: int) -> Task:
    """开发任务竞争认领（Epic 122 切片 2 M1，CAS 并发安全；Story 265 后仅认领 todo）。

    - 条件 UPDATE ``status = todo`` → ``in_progress + assignee_id=user_id``，
      rowcount=1 才成功；并发下另一个写者获胜 → 明确错误（含现状）；
    - 复用 Epic 118 护栏语义：已认领（in_progress/in_review 等）或已结束（done/blocked）
      的任务拒绝重复认领，不创建 Run、不改状态；
    - 认领是「系统操作」，绕开 TRANSITIONS 常规校验；
    - Story 265 收敛：仅 todo 可认领（backlog 已下线，旧 backlog 数据由迁移脚本归并到 todo）。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status != Status.TODO:
        raise InvalidValue(
            f"task {task_id} already claimed or not claimable (status={t.status})")
    old_status = t.status
    r = s.execute(
        update(Task).where(
            Task.id == task_id,
            Task.status == Status.TODO,
        ).values(status=Status.IN_PROGRESS, assignee_id=user_id)
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
        .values(status="done")
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



def list_schedules(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(AgentSchedule).filter(AgentSchedule.project_id == project_id)
    return _paginate(q, limit, offset).all()


