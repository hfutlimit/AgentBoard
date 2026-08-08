import json
import logging
import os
import random
import re
import traceback
from datetime import date, datetime, timedelta
from sqlalchemy import or_, and_, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import models, auth
from .models import (
    ItemType, Status, Priority, SprintStatus, ALL_TYPES, ALL_STATUSES,
    ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES,
    Agent, Project, Epic, Story, Task, Comment, Sprint, Attachment, AgentSchedule, AgentRun,
    ProjectMember, Notification, User, ApiKey, AuditLog, TaskDependency, WebhookConfig,
    Document, DocumentComment, DocumentFolder, ReviewVote, TaskStatusHistory,
    Proposal, ProposalRound, ProposalQuestion,
)
from .domains.projects.models import STORY_REVIEW_STATUSES
from .domains.documents.models import (
    DocumentStatus, DocumentType,
    ALL_DOCUMENT_TYPES, ALL_DOCUMENT_STATUSES, DOCUMENT_TRANSITIONS,
)
from .domains.proposals.models import (
    ProposalStatus, ALL_PROPOSAL_STATUSES, PROPOSAL_TRANSITIONS, ASKABLE_STATUSES,
    CLAIMABLE_STATUSES,
)
from .domains.common.models import utc_now

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200

log = logging.getLogger(__name__)


def _parse_due_date(value):
    """Convert ISO date string (YYYY-MM-DD) to date object; pass through None/date."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise InvalidValue(f"invalid due_date format: {value!r}, expected YYYY-MM-DD")

# 合法状态迁移
# 注：允许从 TODO / IN_PROGRESS 直接标记完成(DONE)，以及 DONE 直接重新打开(TODO)，
# 以支持任务列表/看板的「快速完成」勾选（A-22）。未改变 API 契约，仅放宽迁移规则。
# Epic 123 扩展：
# - in_review → final_review（最终评审，后段共用）；
# - done → blocked（blocked 全向可达，由 set_status 统一特判）；
# - 解除 blocked 恢复到 previous_status 由 set_status 动态处理（不在此表）。
TRANSITIONS = {
    Status.BACKLOG: {Status.TODO, Status.BLOCKED},
    Status.TODO: {Status.IN_PROGRESS, Status.BACKLOG, Status.DONE, Status.BLOCKED},
    Status.IN_PROGRESS: {Status.IN_REVIEW, Status.VERIFYING, Status.TODO, Status.DONE, Status.BLOCKED},
    Status.IN_REVIEW: {Status.DONE, Status.IN_PROGRESS, Status.BLOCKED, Status.FINAL_REVIEW},
    Status.FINAL_REVIEW: {Status.DONE, Status.IN_REVIEW, Status.BLOCKED},
    Status.VERIFYING: {Status.DONE, Status.IN_PROGRESS, Status.BLOCKED},
    Status.DONE: {Status.IN_PROGRESS, Status.TODO, Status.BLOCKED},
    Status.BLOCKED: {Status.TODO, Status.IN_PROGRESS},
}

# 设计评审段（仅 needs_design=true 注入）：todo 必须先进 in_design（不能直跳 in_progress）
_DESIGN_SEGMENT = {
    Status.IN_DESIGN: {Status.DESIGN_PENDING_REVIEW, Status.TODO, Status.BLOCKED},
    Status.DESIGN_PENDING_REVIEW: {Status.DESIGN_REVIEW_APPROVED, Status.IN_DESIGN, Status.BLOCKED},
    Status.DESIGN_REVIEW_APPROVED: {Status.IN_PROGRESS, Status.IN_DESIGN, Status.BLOCKED},
}


def transitions_for(needs_design: bool) -> dict:
    """按 Story.needs_design 返回任务适用的迁移表（Epic 123）。

    - needs_design=true：TODO 出边改为 {IN_DESIGN, BACKLOG, DONE, BLOCKED}（必须先进设计评审段）；
    - needs_design=false：快速流（TODO → IN_PROGRESS）；
    - blocked 全向可达与解除恢复 previous_status 由 set_status 动态处理，不在此表特判。
    """
    if not needs_design:
        return TRANSITIONS
    merged = {k: set(v) for k, v in TRANSITIONS.items()}
    merged[Status.TODO] = {Status.IN_DESIGN, Status.BACKLOG, Status.DONE, Status.BLOCKED}
    for src, targets in _DESIGN_SEGMENT.items():
        merged.setdefault(src, set()).update(targets)
    return merged

EDITABLE = {
    "name", "key", "description", "is_private",   # project
    "title", "description", "status",      # epic / story / task
    "type", "spec", "priority", "sprint_id",  # task
    # Epic 17: 任务管理增强
    "assignee_id", "due_date", "labels", "estimate",
}


def _ser(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[c.name] = v
    return out


def _required(value: str, field: str, max_length: int) -> str:
    value = (value or "").strip()
    if not value:
        raise InvalidValue(f"{field} is required")
    if len(value) > max_length:
        raise InvalidValue(f"{field} must be at most {max_length} characters")
    return value


def _check_type(value: str) -> None:
    if value not in ALL_TYPES:
        raise InvalidValue(f"invalid type '{value}'")


def _check_status(value: str) -> None:
    if value not in ALL_STATUSES:
        raise InvalidValue(f"invalid status '{value}'")


def _paginate(q, limit: int | None, offset: int):
    if offset < 0:
        raise InvalidValue("offset must be non-negative")
    actual_limit = DEFAULT_PAGE_SIZE if limit is None else limit
    if actual_limit < 1 or actual_limit > MAX_PAGE_SIZE:
        raise InvalidValue(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    return q.limit(actual_limit).offset(offset)


def _commit(s: Session, *, duplicate: str | None = None) -> None:
    try:
        s.flush()
        if s.info.get("auto_commit", True):
            s.commit()
    except IntegrityError as exc:
        s.rollback()
        if duplicate:
            raise Duplicate(duplicate) from exc
        raise InvalidValue("database constraint violated") from exc


# ---------- Project ----------
def create_project(s: Session, *, name: str, key=None, description: str = "", is_private: bool | None = None) -> Project:
    name = _required(name, "name", 200)
    key = (key or "").strip() or None
    if key and len(key) > 20:
        raise InvalidValue("key must be at most 20 characters")
    p = Project(name=name, key=key, description=description or "")
    # 2026-07-21: 所有项目默认为邀请制（is_private=True）
    p.is_private = True
    s.add(p)
    _commit(s, duplicate=f"project key '{key}' already exists" if key else None)
    s.refresh(p)
    return p


def get_project(s: Session, id: int) -> Project | None:
    return s.get(Project, id)


def list_projects(s: Session, limit: int | None = None, offset: int = 0):
    q = s.query(Project).order_by(Project.id.desc())
    return _paginate(q, limit, offset).all()


def update_project(s: Session, id: int, **fields) -> Project | None:
    p = s.get(Project, id)
    if not p:
        return None
    for k, v in fields.items():
        if k == "is_private" and v is not None:
            p.is_private = bool(v)
        elif k in ("name", "key", "description") and v is not None:
            if k == "name":
                v = _required(v, "name", 200)
            elif k == "key":
                v = v.strip() or None
                if v and len(v) > 20:
                    raise InvalidValue("key must be at most 20 characters")
            setattr(p, k, v)
    _commit(s, duplicate=f"project key '{p.key}' already exists" if p.key else None)
    s.refresh(p)
    return p


def delete_project(s: Session, id: int) -> bool:
    p = s.get(Project, id)
    if not p:
        return False

    document_ids = [x[0] for x in s.query(Document.id).filter(Document.project_id == id).all()]
    if document_ids:
        s.query(DocumentComment).filter(
            DocumentComment.document_id.in_(document_ids)
        ).delete(synchronize_session=False)
        s.query(Document).filter(Document.id.in_(document_ids)).delete(synchronize_session=False)

    proposal_ids = [x[0] for x in s.query(Proposal.id).filter(Proposal.project_id == id).all()]
    if proposal_ids:
        s.query(ProposalQuestion).filter(
            ProposalQuestion.proposal_id.in_(proposal_ids)
        ).delete(synchronize_session=False)
        s.query(ProposalRound).filter(
            ProposalRound.proposal_id.in_(proposal_ids)
        ).delete(synchronize_session=False)
        s.query(Proposal).filter(Proposal.id.in_(proposal_ids)).delete(synchronize_session=False)

    schedule_ids = [
        x[0] for x in s.query(AgentSchedule.id).filter(AgentSchedule.project_id == id).all()
    ]
    if schedule_ids:
        s.query(AgentRun).filter(AgentRun.schedule_id.in_(schedule_ids)).delete(
            synchronize_session=False
        )
        s.query(AgentSchedule).filter(AgentSchedule.id.in_(schedule_ids)).delete(
            synchronize_session=False
        )

    epic_ids = [x[0] for x in s.query(Epic.id).filter(Epic.project_id == id).all()]
    story_ids = []
    if epic_ids:
        story_ids = [x[0] for x in s.query(Story.id).filter(Story.epic_id.in_(epic_ids)).all()]
    task_filter = Task.project_id == id
    if story_ids:
        task_filter = or_(task_filter, Task.story_id.in_(story_ids))
    task_ids = [x[0] for x in s.query(Task.id).filter(task_filter).all()]
    if task_ids:
        s.query(AgentRun).filter(AgentRun.task_id.in_(task_ids)).update(
            {AgentRun.task_id: None}, synchronize_session=False,
        )
        s.query(Task).filter(Task.source_spec_id.in_(task_ids)).update(
            {Task.source_spec_id: None}, synchronize_session=False,
        )
        s.query(TaskDependency).filter(or_(
            TaskDependency.task_id.in_(task_ids),
            TaskDependency.depends_on_id.in_(task_ids),
        )).delete(synchronize_session=False)
        s.query(Attachment).filter(Attachment.task_id.in_(task_ids)).delete(
            synchronize_session=False
        )
        s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
        s.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session=False)
    if story_ids:
        s.query(Story).filter(Story.id.in_(story_ids)).delete(synchronize_session=False)
    s.query(Epic).filter(Epic.project_id == id).delete(synchronize_session=False)
    s.query(Sprint).filter(Sprint.project_id == id).delete(synchronize_session=False)
    s.query(ProjectMember).filter(ProjectMember.project_id == id).delete(synchronize_session=False)
    s.query(WebhookConfig).filter(WebhookConfig.project_id == id).delete(synchronize_session=False)
    s.delete(p); _commit(s); return True


# ---------- Epic ----------
def create_epic(s: Session, *, project_id: int, title: str, description: str = "") -> Epic:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    ep = Epic(project_id=project_id, title=_required(title, "title", 300), description=description or "")
    s.add(ep); _commit(s); s.refresh(ep); return ep


def get_epic(s: Session, id: int) -> Epic | None:
    return s.get(Epic, id)


def list_epics(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Epic).filter(Epic.project_id == project_id)
    return _paginate(q, limit, offset).all()


def update_epic(s: Session, id: int, **fields) -> Epic | None:
    ep = s.get(Epic, id)
    if not ep:
        return None
    for k, v in fields.items():
        if k in ("title", "description", "status") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            elif k == "status":
                _check_status(v)
            setattr(ep, k, v)
    _commit(s); s.refresh(ep); return ep


def delete_epic(s: Session, id: int) -> bool:
    ep = s.get(Epic, id)
    if not ep:
        return False
    for st in s.query(Story).filter(Story.epic_id == id):
        task_ids = [x[0] for x in s.query(Task.id).filter(Task.story_id == st.id).all()]
        if task_ids:
            s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
        s.query(Task).filter(Task.story_id == st.id).delete()
    s.query(Comment).filter(Comment.story_id.in_(
        s.query(Story.id).filter(Story.epic_id == id)
    )).delete(synchronize_session=False)
    s.query(Comment).filter(Comment.epic_id == id).delete(synchronize_session=False)
    s.query(Story).filter(Story.epic_id == id).delete()
    s.delete(ep); _commit(s); return True


# ---------- Story ----------
def create_story(s: Session, *, epic_id: int, title: str, description: str = "",
                 needs_design: bool = True) -> Story:
    if not s.get(Epic, epic_id):
        raise NotFound(f"epic {epic_id} not found")
    st = Story(epic_id=epic_id, title=_required(title, "title", 300),
               description=description or "", needs_design=needs_design)
    s.add(st); _commit(s); s.refresh(st); return st


def get_story(s: Session, id: int) -> Story | None:
    return s.get(Story, id)


def list_stories(s: Session, epic_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Story).filter(Story.epic_id == epic_id)
    return _paginate(q, limit, offset).all()


def search_stories(s: Session, q: str, limit: int = 20):
    """全局 Story 关键词搜索（标题/描述），供命令面板等场景使用。"""
    like = f"%{q}%"
    qry = s.query(Story).filter(or_(Story.title.ilike(like), Story.description.ilike(like)))
    qry = qry.order_by(Story.id.desc())
    return qry.limit(limit).all()


def search_epics(s: Session, q: str, limit: int = 20):
    """全局 Epic 关键词搜索（标题/描述），供命令面板等场景使用（Epic v6.13）。"""
    like = f"%{q}%"
    qry = s.query(Epic).filter(or_(Epic.title.ilike(like), Epic.description.ilike(like)))
    qry = qry.order_by(Epic.id.desc())
    return qry.limit(limit).all()


def search_sprints(s: Session, q: str, limit: int = 20):
    """全局 Sprint 关键词搜索（title/goal），供命令面板等场景使用（v6.14）。"""
    like = f"%{q}%"
    qry = s.query(Sprint).filter(or_(Sprint.title.ilike(like), Sprint.goal.ilike(like)))
    qry = qry.order_by(Sprint.id.desc())
    return qry.limit(limit).all()


def update_story(s: Session, id: int, **fields) -> Story | None:
    st = s.get(Story, id)
    if not st:
        return None
    for k, v in fields.items():
        if k in ("title", "description", "status", "needs_design") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            elif k == "status":
                # Story 额外允许评审态（pending_review/ready），Task/Epic 不受影响
                if v not in ALL_STATUSES and v not in STORY_REVIEW_STATUSES:
                    raise InvalidValue(f"invalid status '{v}'")
            setattr(st, k, v)
    _commit(s); s.refresh(st); return st


def delete_story(s: Session, id: int) -> bool:
    st = s.get(Story, id)
    if not st:
        return False
    task_ids = [x[0] for x in s.query(Task.id).filter(Task.story_id == id).all()]
    if task_ids:
        s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
    s.query(Comment).filter(Comment.story_id == id).delete(synchronize_session=False)
    s.query(Task).filter(Task.story_id == id).delete()
    s.delete(st); _commit(s); return True


# ---------- Agent 注册表（Epic 122 S1） ----------
def _parse_json_list(raw: str | None, field: str) -> list:
    """解析 roles/capabilities JSON 数组字符串；非法输入抛 InvalidValue。"""
    raw = (raw or "[]").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise InvalidValue(f"{field} must be a JSON array string")
    if not isinstance(parsed, list):
        raise InvalidValue(f"{field} must be a JSON array string")
    return [str(x) for x in parsed]


def register_agent(s: Session, *, agent_id: str, name: str, roles: str = "[]",
                   capabilities: str = "[]", cli_command: str = "",
                   auth_key: str = "", user_id: int | None = None) -> Agent:
    """注册/更新 Agent（幂等：agent_id 已存在则更新字段）。

    agent_id 为外部 Agent 自报唯一标识；roles/capabilities 为 JSON 数组串。
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


def get_agent_by_agent_id(s: Session, agent_id: str) -> Agent | None:
    return s.query(Agent).filter(Agent.agent_id == agent_id).first()


def agent_heartbeat(s: Session, agent_id: str, *, user_id: int | None = None) -> Agent | None:
    """心跳保活：置 online=True 并刷新 last_heartbeat。"""
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    if user_id is not None and agent.user_id not in (None, user_id):
        raise InvalidValue("heartbeat rejected: agent belongs to another user")
    agent.online = True
    agent.last_heartbeat = utc_now()
    if user_id is not None and agent.user_id is None:
        agent.user_id = user_id
    _commit(s); s.refresh(agent); return agent


def agent_deregister(s: Session, agent_id: str, *, user_id: int | None = None,
                     is_admin: bool = False) -> Agent | None:
    """注销下线：置 online=False（保留注册记录）。"""
    agent = s.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        return None
    if not is_admin and user_id is not None and agent.user_id not in (None, user_id):
        raise InvalidValue("deregister rejected: agent belongs to another user")
    agent.online = False
    _commit(s); s.refresh(agent); return agent


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


def get_review_mode() -> str:
    """评审模式：环境变量 AGENTBOARD_REVIEW_MODE（single|majority），非法回退 single。"""
    mode = os.environ.get("AGENTBOARD_REVIEW_MODE", "").strip().lower()
    return mode if mode in (REVIEW_MODE_SINGLE, REVIEW_MODE_MAJORITY) else REVIEW_MODE_SINGLE


def get_review_quorum() -> int:
    """法定票数：AGENTBOARD_REVIEW_QUORUM（2..9），非法/缺省回退 3。"""
    raw = os.environ.get("AGENTBOARD_REVIEW_QUORUM", "").strip()
    try:
        q = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_QUORUM
    return q if 2 <= q <= 9 else DEFAULT_REVIEW_QUORUM


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


def _review_vote_counts(s: Session, entity_type: str, entity_id: int) -> tuple[int, int]:
    """返回 (approve, reject) 票数。"""
    rows = s.query(ReviewVote.verdict, func.count(ReviewVote.id)).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).group_by(ReviewVote.verdict).all()
    counts = dict(rows)
    return int(counts.get("approve", 0)), int(counts.get("reject", 0))


def _clear_review_votes(s: Session, entity_type: str, entity_id: int) -> None:
    """结算后清票（终态 / 驳回后开新一轮，MVP 简化：历史票不跨轮保留）。"""
    s.query(ReviewVote).filter(
        ReviewVote.entity_type == entity_type,
        ReviewVote.entity_id == entity_id,
    ).delete(synchronize_session=False)
    _commit(s)


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
        ).values(status=Status.DONE))
    if r.rowcount != 1:
        s.rollback()
        raise InvalidValue("review conflict: entity state changed concurrently")
    if entity_type == "task":
        _record_status_history(s, entity.id, str(Status.IN_REVIEW), str(Status.DONE),
                               reason="majority approve")
    _commit(s)
    _clear_review_votes(s, entity_type, entity.id)
    return s.get(type(entity), entity.id)


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
        ).values(review_round=new_round, status=target))
    if r.rowcount != 1:
        s.rollback()
        raise InvalidValue("review conflict: entity state changed concurrently")
    if entity_type == "task":
        _record_status_history(s, entity.id, str(Status.IN_REVIEW), str(target),
                               reason=f"majority reject round={new_round}")
    _commit(s)
    _clear_review_votes(s, entity_type, entity.id)
    return s.get(type(entity), entity.id)


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


def _online_reviewer_candidates(s: Session, project_id: int) -> list[Agent]:
    """在线 ∩ 角色含 reviewer ∩ 绑定 user 属项目成员 的 Agent 候选集。"""
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


def assign_reviewer(s: Session, story_id: int, *, user_id: int | None = None,
                    is_admin: bool = False) -> Story:
    """随机指派评审人（显式触发；幂等：已指派则复用）。

    CAS：条件 UPDATE ``status=backlog AND reviewer_id IS NULL`` → ``pending_review + reviewer_id``，
    rowcount=1 才成功；并发下另一个写者获胜时回查返回其指派结果。
    Story 创建默认 backlog，评审流由本函数显式开启（兼容 Epic 96 转化链路）。
    """
    st = s.get(Story, story_id)
    if not st:
        raise NotFound(f"story {story_id} not found")
    if st.reviewer_id is not None:
        return st  # 幂等：已指派
    epic = s.get(Epic, st.epic_id)
    project_id = epic.project_id if epic else None
    if project_id is None:
        raise NotFound(f"epic {st.epic_id} not found")
    candidates = _online_reviewer_candidates(s, project_id)
    if not candidates:
        raise InvalidValue("no online reviewer available (register an online reviewer agent first)")
    reviewer = random.choice(candidates)
    r = s.execute(
        update(Story).where(
            Story.id == story_id,
            Story.reviewer_id.is_(None),
            Story.status == Status.BACKLOG,
        ).values(reviewer_id=reviewer.user_id, status="pending_review")
    )
    if r.rowcount != 1:
        # 并发写者已抢先指派：回查返回现态
        s.rollback()
        return s.get(Story, story_id)
    _commit(s)
    s.refresh(st)
    return st


def review_story(s: Session, *, story_id: int, reviewer_user_id: int,
                 verdict: str, comment: str) -> Story:
    """评审投票（CAS）：仅被指派 reviewer 可操作 pending_review 状态的 Story。

    - approve：状态 → ready（评论记录评审意见，发故事就绪语义）；
    - reject ：review_round + 1，仍停留 pending_review（评论往返收敛）；
    - 护栏：review_round 达 MAX_REVIEW_ROUNDS → blocked（待人工仲裁）。
    - S3 M3：review_mode=majority 时改为多数决投票（_vote_majority），
      投票人资格放宽为项目在线 reviewer 候选，达法定票数按多数结算。

    评论是评审意见唯一载体（approve/reject 必须伴随 comment），形成审计轨迹。
    """
    st = s.get(Story, story_id)
    if not st:
        raise NotFound(f"story {story_id} not found")
    if verdict not in ("approve", "reject"):
        raise InvalidValue(f"invalid verdict '{verdict}' (expected approve|reject)")
    comment = (comment or "").strip()
    if not comment:
        raise InvalidValue("review comment is required (approve/reject must carry a comment)")
    # S3 M3：多数决模式走投票分支（未达法定票数不结算，状态保持）
    if get_review_mode() == REVIEW_MODE_MAJORITY:
        st, _settled = _vote_majority(
            s, st, entity_type="story", reviewer_user_id=reviewer_user_id,
            verdict=verdict, comment=comment)
        return st
    if st.reviewer_id != reviewer_user_id:
        raise InvalidValue("only the assigned reviewer can review this story")
    if st.status != "pending_review":
        raise InvalidValue(f"story is not pending_review (current status: {st.status})")
    reviewer = s.get(User, reviewer_user_id)
    author_name = reviewer.display_name or reviewer.username if reviewer else f"user#{reviewer_user_id}"

    if verdict == "approve":
        r = s.execute(
            update(Story).where(
                Story.id == story_id,
                Story.reviewer_id == reviewer_user_id,
                Story.status == "pending_review",
            ).values(status="ready")
        )
        if r.rowcount != 1:
            s.rollback()
            raise InvalidValue("review conflict: story state changed concurrently")
        _commit(s)
    else:  # reject
        new_round = (st.review_round or 0) + 1
        target = "blocked" if new_round >= MAX_REVIEW_ROUNDS else "pending_review"
        r = s.execute(
            update(Story).where(
                Story.id == story_id,
                Story.reviewer_id == reviewer_user_id,
                Story.status == "pending_review",
            ).values(review_round=new_round, status=target)
        )
        if r.rowcount != 1:
            s.rollback()
            raise InvalidValue("review conflict: story state changed concurrently")
        _commit(s)
    # 评审意见落评论（唯一载体）
    create_comment(s, author=author_name, content=comment, story_id=story_id)
    s.refresh(st)
    return st


def list_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的评审任务（Story，按 pending_review 优先排序）。"""
    q = s.query(Story).filter(Story.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES and status not in STORY_REVIEW_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Story.status == status)
    q = q.order_by(Story.status.desc(), Story.id.desc())
    return q.all()


# ---------- Task ----------
def create_task(s: Session, *, project_id: int, story_id: int | None, title: str,
                type: str = ItemType.TASK, description: str = "", spec: str = "",
                priority: str = Priority.MEDIUM, sprint_id: int | None = None,
                assignee_id: int | None = None, due_date=None, labels: str = "[]",
                estimate: float | None = None) -> Task:
    project = s.get(Project, project_id)
    if not project:
        raise NotFound(f"project {project_id} not found")
    if story_id is not None:
        story = s.get(Story, story_id)
        if not story:
            raise NotFound(f"story {story_id} not found")
        epic = s.get(Epic, story.epic_id)
        if epic is None or epic.project_id != project_id:
            raise InvalidValue(f"story {story_id} does not belong to project {project_id}")
    _check_type(type)
    _check_priority(priority)
    if sprint_id is not None:
        sp = s.get(Sprint, sprint_id)
        if not sp or sp.project_id != project_id:
            raise InvalidValue(f"sprint {sprint_id} does not belong to project {project_id}")
        if sp.status == SprintStatus.COMPLETED:
            raise InvalidValue("cannot assign task to a completed sprint")
    # Epic 17: 验证 assignee_id
    if assignee_id is not None:
        user = s.get(User, assignee_id)
        if not user:
            raise InvalidValue(f"assignee {assignee_id} not found")
    # Epic 17: 验证 labels (JSON)
    import json
    if labels:
        try:
            json.loads(labels)
        except json.JSONDecodeError:
            raise InvalidValue("labels must be a valid JSON array")
    t = Task(project_id=project_id, story_id=story_id, sprint_id=sprint_id,
             title=_required(title, "title", 300),
             type=type, description=description or "", spec=spec or "", priority=priority,
             assignee_id=assignee_id, due_date=_parse_due_date(due_date), labels=labels or "[]",
             estimate=estimate)
    s.add(t); _commit(s); s.refresh(t)
    _invalidate_project_stats_cache(project_id)
    return t


def get_task(s: Session, id: int) -> Task | None:
    return s.get(Task, id)


def list_tasks(s: Session, story_id: int | None = None, sprint_id: int | None = None,
               limit: int | None = None, offset: int = 0):
    q = s.query(Task)
    if story_id is not None:
        q = q.filter(Task.story_id == story_id)
    if sprint_id is not None:
        q = q.filter(Task.sprint_id == sprint_id)
    q = q.order_by(Task.id.desc())
    return _paginate(q, limit, offset).all()


def query_task_count(s: Session, story_id: int | None = None, sprint_id: int | None = None) -> int:
    """返回满足条件的任务总数（用于分页）"""
    q = s.query(func.count(Task.id))
    if story_id is not None:
        q = q.filter(Task.story_id == story_id)
    if sprint_id is not None:
        q = q.filter(Task.sprint_id == sprint_id)
    return q.scalar() or 0


def update_task(s: Session, id: int, **fields) -> Task | None:
    t = s.get(Task, id)
    if not t:
        return None
    allowed = {"title", "description", "spec", "type", "status", "priority", "sprint_id",
               "assignee_id", "due_date", "labels", "estimate"}  # Epic 17 / Epic 32
    nullable_fields = {"due_date", "sprint_id", "assignee_id", "estimate"}  # fields that can be set to None
    for k, v in fields.items():
        if k not in allowed:
            continue
        if v is None and k not in nullable_fields:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "priority":
            _check_priority(v)
        elif k == "type":
            _check_type(v)
        elif k == "status":
            _check_status(v)
        elif k == "sprint_id":
            if v is not None:
                sp = s.get(Sprint, v)
                if not sp or sp.project_id != t.project_id:
                    raise InvalidValue(f"sprint {v} does not belong to project {t.project_id}")
                if sp.status == SprintStatus.COMPLETED:
                    raise InvalidValue("cannot assign task to a completed sprint")
        elif k == "assignee_id":
            if v is not None:
                user = s.get(User, v)
                if not user:
                    raise InvalidValue(f"assignee {v} not found")
        elif k == "due_date":
            v = _parse_due_date(v)
        elif k == "labels":
            import json
            try:
                json.loads(v)
            except json.JSONDecodeError:
                raise InvalidValue("labels must be a valid JSON array")
        setattr(t, k, v)
    _commit(s); s.refresh(t)
    # 关键字段变更时清除项目统计缓存（Epic 23 Story 23.1）
    if any(k in fields for k in ("status", "sprint_id", "priority")):
        _invalidate_project_stats_cache(t.project_id)
    return t


def delete_task(s: Session, id: int) -> bool:
    t = s.get(Task, id)
    if not t:
        return False
    pid = t.project_id
    s.query(AgentRun).filter(AgentRun.task_id == id).update(
        {AgentRun.task_id: None}, synchronize_session=False,
    )
    s.query(Task).filter(Task.source_spec_id == id).update(
        {Task.source_spec_id: None}, synchronize_session=False,
    )
    s.query(TaskDependency).filter(or_(
        TaskDependency.task_id == id,
        TaskDependency.depends_on_id == id,
    )).delete(synchronize_session=False)
    s.query(Attachment).filter(Attachment.task_id == id).delete(synchronize_session=False)
    s.query(Comment).filter(Comment.task_id == id).delete(synchronize_session=False)
    s.delete(t); _commit(s)
    _invalidate_project_stats_cache(pid)
    return True


def set_task_description(s: Session, id: int, text: str) -> Task | None:
    return update_task(s, id, description=text)


def set_task_spec(s: Session, id: int, text: str) -> Task | None:
    return update_task(s, id, spec=text)


def append_task_spec(s: Session, id: int, text: str) -> Task | None:
    t = s.get(Task, id)
    if not t:
        return None
    t.spec = (t.spec or "") + "\n" + text
    _commit(s); s.refresh(t); return t


def _task_needs_design(s: Session, t: Task) -> bool:
    """Task 所属 Story 是否需要设计评审段（Epic 123）；无 Story 视为快速流（false）。"""
    if t.story_id is None:
        return False
    story = s.get(Story, t.story_id)
    return bool(story and story.needs_design)


def _record_status_history(s: Session, task_id: int, from_status: str, to_status: str,
                           *, changed_by: int | None = None, reason: str = "") -> None:
    """任务状态变更历史（task_status_history）：全部状态变更路径统一调用。"""
    s.add(TaskStatusHistory(
        task_id=task_id, from_status=from_status, to_status=to_status,
        changed_by=changed_by, reason=reason or "",
    ))


def list_task_status_history(s: Session, task_id: int, limit: int = 100):
    """任务状态变更历史（Epic 123），按时间倒序返回。"""
    return (s.query(TaskStatusHistory)
            .filter(TaskStatusHistory.task_id == task_id)
            .order_by(TaskStatusHistory.id.desc())
            .limit(limit).all())


def set_status(s: Session, id: int, new_status: str, *,
               changed_by: int | None = None, reason: str = "") -> Task | None:
    t = s.get(Task, id)
    if not t:
        raise NotFound(f"task {id} not found")
    _check_status(new_status)
    new = Status(new_status)
    current = Status(t.status)
    if current != new:
        if new == Status.BLOCKED:
            pass  # blocked 全向可达：任意状态 → blocked（Epic 123）
        elif current == Status.BLOCKED:
            # 解除阻塞：优先恢复到进入阻塞前的 previous_status
            prev = t.previous_status
            if prev and Status(prev) == new:
                pass
            elif new not in transitions_for(_task_needs_design(s, t)).get(Status.BLOCKED, set()):
                raise IllegalTransition(f"{t.status} -> {new} 不合法")
        elif new not in transitions_for(_task_needs_design(s, t)).get(current, set()):
            raise IllegalTransition(f"{t.status} -> {new} 不合法")
    old_status = t.status
    if old_status != new:
        t.status = new
        if new == Status.BLOCKED:
            t.previous_status = old_status
        elif old_status == Status.BLOCKED:
            t.previous_status = None
        _record_status_history(s, t.id, old_status, str(new), changed_by=changed_by, reason=reason)
        _commit(s)
        s.refresh(t)
        # 状态变更时清除项目统计缓存
        _invalidate_project_stats_cache(t.project_id)
    return t


def claim_development_task(s: Session, task_id: int, *, user_id: int) -> Task:
    """开发任务竞争认领（Epic 122 切片 2 M1，CAS 并发安全）。

    - 条件 UPDATE ``status IN (backlog, todo)`` → ``in_progress + assignee_id=user_id``，
      rowcount=1 才成功；并发下另一个写者获胜 → 明确错误（含现状）；
    - 复用 Epic 118 护栏语义：已认领（in_progress/in_review 等）或已结束（done/blocked）
      的任务拒绝重复认领，不创建 Run、不改状态；
    - 认领是「系统操作」，绕开 TRANSITIONS 常规校验（backlog → in_progress 不在常规表内）。
    """
    t = s.get(Task, task_id)
    if not t:
        raise NotFound(f"task {task_id} not found")
    if t.status not in (Status.BACKLOG, Status.TODO):
        raise InvalidValue(
            f"task {task_id} already claimed or not claimable (status={t.status})")
    old_status = t.status
    r = s.execute(
        update(Task).where(
            Task.id == task_id,
            Task.status.in_([Status.BACKLOG, Status.TODO]),
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


def list_task_review_tasks(s: Session, user_id: int, *, status: str | None = None):
    """拉取指派给当前用户的 Task 评审任务（按 in_review 优先排序）。"""
    q = s.query(Task).filter(Task.reviewer_id == user_id)
    if status:
        if status not in ALL_STATUSES:
            raise InvalidValue(f"invalid status '{status}'")
        q = q.filter(Task.status == status)
    q = q.order_by(Task.status.desc(), Task.id.desc())
    return q.all()


# ---------- 评审统计与超时护栏（Epic 122 S3 M2） ----------
DEFAULT_REVIEW_TIMEOUT_MINUTES = 30
DEFAULT_TIMEOUT_SCAN_BATCH = 20


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
    reviewer = random.choice(candidates)
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
    reviewer = random.choice(candidates)
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


def get_review_stats(s: Session, *, project_id: int, days: int = 7,
                     user_id: int | None = None) -> dict:
    """项目级评审统计运营视图（S3 M2）。

    口径（见 design.md §4）：
    - story approved = status=ready 且 reviewer 已指派；rejected = review_round>0；
      pending = pending_review；blocked = blocked；
    - task approved = done 且 reviewer 已指派；rejected = review_round>0；
      pending = in_review；blocked = blocked；
    - reject_rate = rejected / (approved + rejected)，分母 0 → 0.0；
    - by_reviewer：按 reviewer_id 聚合评审工作量（approve/reject 分布）；
    - days 过滤 created_at ≥ now-days；user_id 过滤仅统计该评审人条目。
    """
    project = s.get(Project, project_id)
    if not project:
        raise NotFound(f"project {project_id} not found")
    days = max(0, int(days)) if days is not None else 7
    since = utc_now() - timedelta(days=days) if days > 0 else None

    st_q = s.query(Story).join(Epic, Story.epic_id == Epic.id).filter(
        Epic.project_id == project_id)
    t_q = s.query(Task).filter(Task.project_id == project_id)
    if since is not None:
        st_q = st_q.filter(Story.created_at >= since)
        t_q = t_q.filter(Task.created_at >= since)
    if user_id is not None:
        st_q = st_q.filter(Story.reviewer_id == user_id)
        t_q = t_q.filter(Task.reviewer_id == user_id)
    stories = st_q.all()
    tasks = t_q.all()

    def _buckets(items, *, is_story):
        approved = rejected = pending = blocked = 0
        rounds, round_n = 0, 0
        for it in items:
            status = it.status
            reviewed = it.reviewer_id is not None or (it.review_round or 0) > 0
            if is_story:
                if status == "ready" and it.reviewer_id is not None:
                    approved += 1
                if status == "pending_review":
                    pending += 1
            else:
                if status == Status.DONE and it.reviewer_id is not None:
                    approved += 1
                if status == Status.IN_REVIEW:
                    pending += 1
            if status == Status.BLOCKED or status == "blocked":
                blocked += 1
            if (it.review_round or 0) > 0:
                rejected += 1
            if reviewed:
                rounds += it.review_round or 0
                round_n += 1
        return {
            "total": len(items),
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "blocked": blocked,
            "_rounds": rounds,
            "_round_n": round_n,
        }

    sb = _buckets(stories, is_story=True)
    tb = _buckets(tasks, is_story=False)

    def _avg(b):
        return round(b["_rounds"] / b["_round_n"], 2) if b["_round_n"] else 0.0

    # timeout_pending：当前超时未决数（默认 30min 口径）
    timeout = timedelta(minutes=DEFAULT_REVIEW_TIMEOUT_MINUTES)
    now = utc_now()
    timeout_pending = 0
    for st in stories:
        if st.status == "pending_review" and st.reviewer_id is not None \
                and now - _story_last_activity(s, st) > timeout:
            timeout_pending += 1
    for t in tasks:
        if t.status == Status.IN_REVIEW and t.reviewer_id is not None \
                and now - t.updated_at > timeout:
            timeout_pending += 1

    # by_reviewer 聚合
    agg: dict[int, dict] = {}
    for it in list(stories) + list(tasks):
        rid = it.reviewer_id
        if rid is None:
            continue
        row = agg.setdefault(rid, {
            "user_id": rid, "name": None,
            "story_reviewed": 0, "task_reviewed": 0,
            "story_approved": 0, "story_rejected": 0,
            "task_approved": 0, "task_rejected": 0,
        })
        if it.__class__ is Story:
            row["story_reviewed"] += 1
            if it.status == "ready":
                row["story_approved"] += 1
            if (it.review_round or 0) > 0:
                row["story_rejected"] += 1
        else:
            row["task_reviewed"] += 1
            if it.status == Status.DONE:
                row["task_approved"] += 1
            if (it.review_round or 0) > 0:
                row["task_rejected"] += 1
    by_reviewer = []
    for rid, row in agg.items():
        u = s.get(User, rid)
        row["name"] = u.display_name or u.username if u else f"user#{rid}"
        by_reviewer.append(row)
    by_reviewer.sort(key=lambda r: -(r["story_reviewed"] + r["task_reviewed"]))

    # S4 M2：多数决评审投票进度（review_mode/quorum/votes）
    # - review_mode：single|majority（env 驱动）；review_quorum：法定票数；
    # - majority 模式下 votes 列出全部 pending 实体（pending_review Story /
    #   in_review Task）的已投票数（approve/reject/cast）与 quorum；
    # - single 模式 votes 恒为空数组（零行为变化）。
    review_mode = get_review_mode()
    review_quorum = get_review_quorum()
    vote_rows: list[dict] = []
    if review_mode == REVIEW_MODE_MAJORITY:
        for st in stories:
            if st.status != "pending_review":
                continue
            approve_n, reject_n = _review_vote_counts(s, "story", st.id)
            vote_rows.append({
                "kind": "story",
                "id": st.id,
                "title": st.title,
                "status": st.status,
                "approve": approve_n,
                "reject": reject_n,
                "cast": approve_n + reject_n,
                "quorum": review_quorum,
            })
        for t in tasks:
            if t.status != Status.IN_REVIEW:
                continue
            approve_n, reject_n = _review_vote_counts(s, "task", t.id)
            vote_rows.append({
                "kind": "task",
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "approve": approve_n,
                "reject": reject_n,
                "cast": approve_n + reject_n,
                "quorum": review_quorum,
            })

    total_done = sb["approved"] + sb["rejected"] + tb["approved"] + tb["rejected"]
    total_rejected = sb["rejected"] + tb["rejected"]
    return {
        "project_id": project_id,
        "days": days,
        "stories": {k: sb[k] for k in ("total", "approved", "rejected", "pending", "blocked")},
        "tasks": {k: tb[k] for k in ("total", "approved", "rejected", "pending", "blocked")},
        "rounds": {"avg_story_round": _avg(sb), "avg_task_round": _avg(tb)},
        "reject_rate": round(total_rejected / total_done, 4) if total_done else 0.0,
        "timeout_pending": timeout_pending,
        "by_reviewer": by_reviewer,
        "review_mode": review_mode,
        "review_quorum": review_quorum,
        "votes": vote_rows,
        "generated_at": utc_now().isoformat(),
    }


def _invalidate_project_stats_cache(project_id: int) -> None:
    """清除项目统计缓存（Epic 23 Story 23.1）"""
    try:
        from agentboard.cache import get_cache
        cache = get_cache()
        cache.delete(f"project_stats:{project_id}")
    except Exception:
        pass  # 缓存失败不影响主流程


# ---------- Spec -> 子任务（OpenSpec / Superpowers 风格） ----------
def generate_tasks_from_spec(s: Session, task_id: int) -> list:
    """解析任务 spec 中的清单项（- [ ] 标题），生成同级子任务。

    生成的子任务：同 project / story，type=task，status=backlog，
    并通过 source_spec_id 反向关联到源任务；同时在源 spec 末尾回写链接。
    """
    src = s.get(Task, task_id)
    if not src:
        raise NotFound(f"task {task_id} not found")
    existing_titles = {
        title for (title,) in s.query(Task.title).filter(Task.source_spec_id == task_id).all()
    }
    created = []
    for line in (src.spec or "").splitlines():
        m = re.match(r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)", line)
        if not m:
            continue
        title = m.group(1).strip()
        if not title:
            continue
        title = title[:300]
        if title in existing_titles:
            continue
        t = Task(project_id=src.project_id, story_id=src.story_id,
                 type=ItemType.TASK, title=title[:300], description=title,
                 source_spec_id=task_id)
        s.add(t)
        created.append(t)
        existing_titles.add(title)
    if created:
        s.flush()
        links = "\n".join(f"- 子任务 #{t.id}: {t.title}" for t in created)
        src.spec = (src.spec or "") + f"\n\n## 生成的子任务\n{links}\n"
    _commit(s)
    for t in created:
        s.refresh(t)
    if created:
        s.refresh(src)
    return created


# ---------- Search ----------
def search_tasks(s: Session, *, project_id=None, epic_id=None, story_id=None,
                 sprint_id=None, type=None, status=None, priority=None, q=None,
                 reviewer_id: int | None = None,
                 limit: int | None = None, offset: int = 0):
    qry = s.query(Task)
    if project_id is not None:
        qry = qry.filter(Task.project_id == project_id)
    if story_id is not None:
        qry = qry.filter(Task.story_id == story_id)
    if sprint_id is not None:
        qry = qry.filter(Task.sprint_id == sprint_id)
    if type is not None:
        _check_type(type)
        qry = qry.filter(Task.type == type)
    if status is not None:
        _check_status(status)
        qry = qry.filter(Task.status == status)
    if priority is not None:
        _check_priority(priority)
        qry = qry.filter(Task.priority == priority)
    if reviewer_id is not None:
        qry = qry.filter(Task.reviewer_id == reviewer_id)
    if epic_id is not None:
        qry = qry.join(Story, Task.story_id == Story.id).filter(Story.epic_id == epic_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Task.title.ilike(like), Task.description.ilike(like),
                              Task.spec.ilike(like)))
    qry = qry.order_by(Task.id.desc())
    return _paginate(qry, limit, offset).all()


def _check_priority(priority: str) -> None:
    if priority not in ALL_PRIORITIES:
        raise InvalidValue(f"invalid priority '{priority}'")


# ---------- Comments ----------
def _comment_target(
    s: Session, *, task_id: int | None, story_id: int | None, epic_id: int | None
) -> dict:
    """校验评论挂载目标：task/story/epic 三者恰好其一非空，且实体存在。返回 {task_id|story_id|epic_id: id}。"""
    candidates = {"task_id": (Task, task_id), "story_id": (Story, story_id), "epic_id": (Epic, epic_id)}
    present = {k: v for k, v in candidates.items() if v[1] is not None}
    if len(present) != 1:
        raise InvalidValue("exactly one of task_id/story_id/epic_id must be set")
    name, (model, obj_id) = next(iter(present.items()))
    if not s.get(model, obj_id):
        raise NotFound(f"{name.removesuffix('_id')} {obj_id} not found")
    return {name: obj_id}


def create_comment(s: Session, *, author: str, content: str,
                   task_id: int | None = None, story_id: int | None = None,
                   epic_id: int | None = None) -> Comment:
    target = _comment_target(s, task_id=task_id, story_id=story_id, epic_id=epic_id)
    author, content = (author or "").strip(), (content or "").strip()
    if not author or not content:
        raise InvalidValue("author and content are required")
    comment = Comment(author=author[:100], content=content, **target)
    s.add(comment); _commit(s); s.refresh(comment); return comment


def list_comments(s: Session, *, task_id: int | None = None, story_id: int | None = None,
                  epic_id: int | None = None):
    if task_id is not None:
        if not s.get(Task, task_id):
            raise NotFound(f"task {task_id} not found")
        q = s.query(Comment).filter(Comment.task_id == task_id)
    elif story_id is not None:
        if not s.get(Story, story_id):
            raise NotFound(f"story {story_id} not found")
        q = s.query(Comment).filter(Comment.story_id == story_id)
    elif epic_id is not None:
        if not s.get(Epic, epic_id):
            raise NotFound(f"epic {epic_id} not found")
        q = s.query(Comment).filter(Comment.epic_id == epic_id)
    else:
        raise InvalidValue("exactly one of task_id/story_id/epic_id must be set")
    return q.order_by(Comment.created_at, Comment.id).all()


def delete_comment(s: Session, id: int) -> bool:
    comment = s.get(Comment, id)
    if not comment:
        return False
    s.delete(comment); _commit(s); return True


# ---------- Sprint ----------
def _check_sprint_status(status: str) -> None:
    if status not in ALL_SPRINT_STATUSES:
        raise InvalidValue(f"invalid sprint status '{status}'")


def create_sprint(s: Session, *, project_id: int, title: str,
                  goal: str = "", start_date=None, end_date=None) -> Sprint:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    sp = Sprint(project_id=project_id,
                title=_required(title, "title", 300),
                goal=goal or "",
                start_date=start_date, end_date=end_date)
    s.add(sp); _commit(s); s.refresh(sp); return sp


def get_sprint(s: Session, id: int) -> Sprint | None:
    return s.get(Sprint, id)


def list_sprints(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Sprint).filter(Sprint.project_id == project_id)
    return _paginate(q, limit, offset).all()


def get_sprint_burndown(s: Session, sprint_id: int) -> dict:
    """返回 Sprint 燃尽图数据：每日剩余任务数。"""
    from datetime import timedelta, datetime as dt
    from sqlalchemy import func

    sp = s.get(Sprint, sprint_id)
    if not sp:
        raise NotFound(f"sprint {sprint_id} not found")

    # 统计总任务数
    total = s.query(func.count(Task.id)).filter(Task.sprint_id == sprint_id).scalar() or 0

    # 已完成任务数
    done = s.query(func.count(Task.id)).filter(
        Task.sprint_id == sprint_id, Task.status == Status.DONE
    ).scalar() or 0

    # 理想燃尽：从 start_date 每天递减，到 end_date 为 0
    # 如果没有 start_date，从今天往前推 14 天
    today = dt.now().date()
    if sp.start_date:
        start = sp.start_date.date() if hasattr(sp.start_date, 'date') else sp.start_date
    else:
        start = today - timedelta(days=13)
    if sp.end_date:
        end = sp.end_date.date() if hasattr(sp.end_date, 'date') else sp.end_date
    else:
        end = today

    # 生成每日剩余任务数（理想线 = 线性递减）
    days = []
    ideal = []
    total_days = max((end - start).days, 1)
    for i in range(total_days + 1):
        day = start + timedelta(days=i)
        # 剩余 = 总任务 - (i/total_days * 总任务) = 总任务 * (1 - i/total_days)
        ideal_val = round(total * (1 - i / total_days)) if total_days > 0 else 0
        # 实际剩余：统计当天及之前完成的任务
        done_by_day = s.query(func.count(Task.id)).filter(
            Task.sprint_id == sprint_id,
            Task.status == Status.DONE,
            func.date(Task.updated_at) <= day,
        ).scalar() or 0
        remaining = total - done_by_day
        days.append({"day": day.isoformat(), "remaining": remaining, "ideal": ideal_val})

    return {
        "sprint_id": sprint_id,
        "title": sp.title,
        "total_tasks": total,
        "done_tasks": done,
        "remaining_tasks": total - done,
        "start_date": sp.start_date.isoformat() if sp.start_date else start.isoformat(),
        "end_date": sp.end_date.isoformat() if sp.end_date else end.isoformat(),
        "status": sp.status.value if hasattr(sp.status, 'value') else sp.status,
        "daily": days,
    }


def activate_sprint(s: Session, id: int) -> Sprint:
    """激活 Sprint：先停用同项目所有 ACTIVE Sprint，再激活目标 Sprint。"""
    sp = s.get(Sprint, id)
    if not sp:
        raise NotFound(f"sprint {id} not found")
    if sp.status == SprintStatus.COMPLETED:
        raise InvalidValue("cannot activate a completed sprint")
    # 停用同项目所有 ACTIVE Sprint
    s.query(Sprint).filter(
        Sprint.project_id == sp.project_id,
        Sprint.status == SprintStatus.ACTIVE,
        Sprint.id != sp.id
    ).update({"status": SprintStatus.PLANNING})
    sp.status = SprintStatus.ACTIVE
    _commit(s); s.refresh(sp); return sp


def complete_sprint(s: Session, id: int) -> Sprint:
    """完成 Sprint：将其状态改为 completed，未完成任务退回 backlog。"""
    sp = s.get(Sprint, id)
    if not sp:
        raise NotFound(f"sprint {id} not found")
    if sp.status == SprintStatus.COMPLETED:
        raise InvalidValue("sprint is already completed")
    sp.status = SprintStatus.COMPLETED
    # 未完成任务退回 backlog
    s.query(Task).filter(
        Task.sprint_id == sp.id,
        Task.status.notin_([Status.DONE])
    ).update({"sprint_id": None, "status": Status.BACKLOG})
    _commit(s); s.refresh(sp); return sp


def update_sprint(s: Session, id: int, **fields) -> Sprint | None:
    sp = s.get(Sprint, id)
    if not sp:
        return None
    for k, v in fields.items():
        if k in ("title", "goal") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            setattr(sp, k, v)
        elif k == "start_date" and v is not None:
            sp.start_date = v
        elif k == "end_date" and v is not None:
            sp.end_date = v
    _commit(s); s.refresh(sp); return sp


def delete_sprint(s: Session, id: int) -> bool:
    sp = s.get(Sprint, id)
    if not sp:
        return False
    if sp.status == SprintStatus.ACTIVE:
        raise InvalidValue("cannot delete an active sprint")
    # 将关联任务解除绑定
    s.query(Task).filter(Task.sprint_id == sp.id).update({"sprint_id": None})
    s.delete(sp); _commit(s); return True


def _now():
    from datetime import datetime, UTC
    return datetime.now(UTC).replace(tzinfo=None)


# ---------- Attachment ----------
import os as _os
import uuid as _uuid

ATTACHMENT_DIR = _os.getenv("AGENTBOARD_ATTACHMENT_DIR", "data/attachments")
ATTACHMENT_MAX_SIZE = int(_os.getenv("AGENTBOARD_ATTACHMENT_MAX_SIZE", str(10 * 1024 * 1024)))  # 10 MB
ATTACHMENT_ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "application/pdf",
    "text/plain", "text/markdown", "text/csv",
    "application/json", "application/xml",
    "application/zip", "application/gzip",
}


def _attachment_dir() -> str:
    _os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    return ATTACHMENT_DIR


def create_attachment(s: Session, *, task_id: int, content: bytes, original_name: str, mime_type: str) -> Attachment:
    if not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    if mime_type not in ATTACHMENT_ALLOWED_TYPES:
        raise InvalidValue(f"unsupported MIME type: {mime_type}")
    if len(content) > ATTACHMENT_MAX_SIZE:
        raise InvalidValue(f"file exceeds {ATTACHMENT_MAX_SIZE // (1024*1024)} MB limit")
    stored = _uuid.uuid4().hex
    path = _os.path.join(_attachment_dir(), stored)
    with open(path, "wb") as f:
        f.write(content)
    att = Attachment(task_id=task_id, filename=stored, original_name=original_name,
                     size=len(content), mime_type=mime_type)
    s.add(att); _commit(s); s.refresh(att); return att


def get_attachment(s: Session, id: int) -> Attachment | None:
    return s.get(Attachment, id)


def get_attachment_path(att: Attachment) -> str:
    return _os.path.join(ATTACHMENT_DIR, att.filename)


def list_attachments(s: Session, task_id: int) -> list:
    if not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    return s.query(Attachment).filter(Attachment.task_id == task_id).order_by(Attachment.id).all()


def delete_attachment(s: Session, id: int) -> bool:
    att = s.get(Attachment, id)
    if not att:
        return False
    path = _os.path.join(ATTACHMENT_DIR, att.filename)
    if _os.path.isfile(path):
        _os.unlink(path)
    s.delete(att); _commit(s); return True


# ---------- AgentSchedule / AgentRun ----------
import re as _re

_CRON_PATTERN = _re.compile(
    # 支持 */n 步长语法（如 */1 每分钟，*/5 每5分钟）
    r"^(\*(?:/\d+)?|[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?(?:,[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?)*)\s+"
    r"(\*(?:/\d+)?|1?\d|2[0-3])(?:-[1-2]?\d(?:/\d+)?)?(?:,(?:1?\d|#[0-3]))*\s+"
    r"(\*(?:/\d+)?|[1-2]?\d|3[01])(?:-[1-3]?\d(?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|1?\d|1[0-2])(?:-1[0-2](?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|[0-7])(?:-[0-7](?:/\d+)?)?(?:,[0-7](?:-[0-7](?:/\d+)?)?)*$"
)


def _validate_cron(expr: str) -> None:
    """校验 cron 表达式格式（5 字段：分 时 日 月 周）。"""
    if not _CRON_PATTERN.match(expr.strip()):
        raise InvalidValue(f"invalid cron expression: {expr}")


#: 可绑定到 AgentSchedule.agent 的合法 Agent 名（与 executor.KNOWN_AGENTS 对应；
#: 校验仅防手滑，松绑后实际分发由 executor 注册表决定）
SCHEDULE_AGENTS = ("codex", "claude", "workbuddy", "qoder")

#: 任务优先级权重（值越大优先级越高；用于 pick_eligible_task 排序与门槛）
PRIORITY_RANK = {
    Priority.HIGHEST: 5, Priority.HIGH: 4, Priority.MEDIUM: 3,
    Priority.LOW: 2, Priority.LOWEST: 1,
}

#: 执行器可自动领取的任务状态（未开始、可执行的活）
ELIGIBLE_TASK_STATUSES = (Status.BACKLOG, Status.TODO)


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


def pick_eligible_task(s: Session, schedule: AgentSchedule):
    """
    为「项目/Agent 级」schedule 挑选下一个 eligible task。

    规则：
    - 固定 ``task_id`` → 直接返回该 task（存在即返回，兼容旧单任务语义）；
    - 项目级：``status ∈ (backlog, todo)``，按 ``epic_id`` / ``task_type`` 过滤，
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


def get_schedule(s: Session, id: int) -> AgentSchedule | None:
    return s.get(AgentSchedule, id)


def list_schedules(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(AgentSchedule).filter(AgentSchedule.project_id == project_id)
    return _paginate(q, limit, offset).all()


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


def delete_schedule(s: Session, id: int) -> bool:
    sch = s.get(AgentSchedule, id)
    if not sch:
        return False
    s.delete(sch); _commit(s); return True


def create_run(s: Session, *, schedule_id: int, task_id: int | None = None,
               idempotency_key: str | None = None) -> AgentRun:
    if not s.get(AgentSchedule, schedule_id):
        raise NotFound(f"schedule {schedule_id} not found")
    if idempotency_key:
        existing = s.query(AgentRun).filter(AgentRun.idempotency_key == idempotency_key).first()
        if existing:
            raise Duplicate(f"run with idempotency_key '{idempotency_key}' already exists")
    run = AgentRun(schedule_id=schedule_id, task_id=task_id,
                   idempotency_key=idempotency_key)
    s.add(run); _commit(s); s.refresh(run); return run


def get_run(s: Session, id: int) -> AgentRun | None:
    return s.get(AgentRun, id)


def list_runs(s: Session, schedule_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(AgentRun).filter(AgentRun.schedule_id == schedule_id).order_by(AgentRun.id.desc())
    return _paginate(q, limit, offset).all()


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


def delete_run(s: Session, id: int) -> bool:
    run = s.get(AgentRun, id)
    if not run:
        return False
    s.delete(run); _commit(s); return True


class DomainError(Exception):
    pass


class NotFound(DomainError):
    pass


class IllegalTransition(DomainError):
    pass


class Duplicate(DomainError):
    pass


class InvalidValue(DomainError):
    pass


# ---------- Auth ----------
def has_users(s: Session) -> bool:
    return s.query(models.User.id).first() is not None


def register_user(s: Session, *, username: str, password: str) -> models.User:
    username = _required(username, "username", 64)
    if len(password or "") < 8:
        raise InvalidValue("password must be at least 8 characters")
    if s.query(models.User).filter_by(username=username).first():
        raise Duplicate(f"username '{username}' already exists")
    # 第一个注册用户自动成为管理员
    is_first = not has_users(s)
    u = models.User(username=username, password_hash=auth.hash_password(password), is_admin=is_first)
    s.add(u)
    _commit(s, duplicate=f"username '{username}' already exists")
    s.refresh(u)
    return u


def authenticate_user(s: Session, *, username: str, password: str) -> models.User | None:
    u = s.query(models.User).filter_by(username=username).first()
    if u and auth.verify_password(password, u.password_hash):
        if auth.password_needs_rehash(u.password_hash):
            u.password_hash = auth.hash_password(password)
            _commit(s)
        return u
    return None


def get_user(s: Session, id: int) -> models.User | None:
    return s.get(models.User, id)


def get_user_by_username(s: Session, username: str) -> models.User | None:
    return s.query(models.User).filter(models.User.username == username).first()


def update_user_profile(
    s: Session, user: models.User, *, display_name: str | None = None,
    email: str | None = None, avatar_url: str | None = None,
) -> models.User:
    if display_name is not None:
        user.display_name = display_name.strip()[:100]
    if email is not None:
        normalized_email = email.strip().lower() or None
        if normalized_email:
            existing = s.query(models.User).filter(
                models.User.email == normalized_email, models.User.id != user.id,
            ).first()
            if existing:
                raise Duplicate(f"email '{normalized_email}' already exists")
        user.email = normalized_email
    if avatar_url is not None:
        user.avatar_url = avatar_url.strip() or None
    _commit(s, duplicate="email already exists")
    s.refresh(user)
    return user


def change_user_password(
    s: Session, user: models.User, *, current_password: str, new_password: str,
) -> None:
    if not auth.verify_password(current_password, user.password_hash):
        raise InvalidValue("current password is incorrect")
    if len(new_password or "") < 8:
        raise InvalidValue("new password must be at least 8 characters")
    user.password_hash = auth.hash_password(new_password)
    _commit(s)


def create_api_key(s: Session, *, user_id: int, name: str, permissions: list[str]) -> tuple[ApiKey, str]:
    plaintext, prefix, digest = auth.generate_api_key()
    item = ApiKey(
        user_id=user_id, name=name.strip(), key_prefix=prefix, key_hash=digest,
        permissions=auth.encode_permissions(permissions), enabled=True,
    )
    s.add(item)
    _commit(s)
    s.refresh(item)
    return item, plaintext


def list_api_keys(s: Session, *, user_id: int) -> list[ApiKey]:
    return s.query(ApiKey).filter(ApiKey.user_id == user_id).order_by(ApiKey.id.desc()).all()


def get_api_key(s: Session, *, user_id: int, api_key_id: int) -> ApiKey | None:
    return s.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()


def update_api_key(
    s: Session, item: ApiKey, *, name: str | None = None,
    enabled: bool | None = None, permissions: list[str] | None = None,
) -> ApiKey:
    if name is not None:
        item.name = name.strip()
    if enabled is not None:
        item.enabled = enabled
    if permissions is not None:
        item.permissions = auth.encode_permissions(permissions)
    item.updated_at = models._now()
    _commit(s)
    s.refresh(item)
    return item


def revoke_api_key(s: Session, *, user_id: int, api_key_id: int) -> bool:
    item = s.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()
    if item is None:
        return False
    s.delete(item)
    _commit(s)
    return True


def lookup_api_key_by_hash(s: Session, key_hash: str) -> ApiKey | None:
    return s.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()


def touch_api_key(s: Session, item: ApiKey) -> None:
    item.last_used_at = models._now()
    _commit(s)


# ---------- Paged response ----------
def paginated_result(items: list, total: int) -> dict:
    return {"items": items, "total": total}


# ---------- Project visibility helpers ----------
def user_is_project_member(s: Session, project_id: int, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
        is not None
    )


def user_is_project_owner(s: Session, project_id: int, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return (
        s.query(ProjectMember)
        .filter(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.role == "owner",
        )
        .first()
        is not None
    )


# ---------- ProjectMember ----------
def add_project_member(
    s: Session, *, project_id: int, user_id: int, role: str = "member",
) -> ProjectMember:
    """将用户加入项目（自动分配 owner 为创建者，或由管理员添加）"""
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    if not s.get(models.User, user_id):
        raise NotFound(f"user {user_id} not found")
    existing = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if existing:
        raise Duplicate(f"user {user_id} already in project {project_id}")
    if role not in ("owner", "member"):
        raise InvalidValue("role must be 'owner' or 'member'")
    pm = ProjectMember(project_id=project_id, user_id=user_id, role=role)
    s.add(pm); _commit(s); s.refresh(pm); return pm


def list_project_members(s: Session, project_id: int, limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = s.query(ProjectMember).filter(ProjectMember.project_id == project_id)
    total = q.count()
    return _paginate(q.order_by(ProjectMember.joined_at.desc()), limit, offset).all(), total


def remove_project_member(s: Session, project_id: int, user_id: int) -> bool:
    pm = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if not pm:
        return False
    if pm.role == "owner":
        # 检查是否还有其他人是 owner
        owner_count = (
            s.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id, ProjectMember.role == "owner")
            .count()
        )
        if owner_count <= 1:
            raise InvalidValue("cannot remove the last owner from a project")
    s.delete(pm); _commit(s); return True


def update_project_member_role(s: Session, project_id: int, user_id: int, role: str) -> ProjectMember | None:
    pm = (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if not pm:
        return None
    if role not in ("owner", "member"):
        raise InvalidValue("role must be 'owner' or 'member'")
    pm.role = role; _commit(s); s.refresh(pm); return pm


def get_project_member(s: Session, project_id: int, user_id: int) -> ProjectMember | None:
    return (
        s.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )


# ---------- Child-resource -> project resolution (access control) ----------
def get_epic_project_id(s: Session, epic_id: int) -> int | None:
    e = s.get(Epic, epic_id)
    return e.project_id if e else None


def get_story_project_id(s: Session, story_id: int) -> int | None:
    st = s.get(Story, story_id)
    if not st:
        return None
    e = s.get(Epic, st.epic_id)
    return e.project_id if e else None


def get_task_project_id(s: Session, task_id: int) -> int | None:
    t = s.get(Task, task_id)
    if not t:
        return None
    return get_story_project_id(s, t.story_id)


def get_sprint_project_id(s: Session, sprint_id: int) -> int | None:
    sp = s.get(Sprint, sprint_id)
    return sp.project_id if sp else None


def get_schedule_project_id(s: Session, schedule_id: int) -> int | None:
    sch = s.get(AgentSchedule, schedule_id)
    return sch.project_id if sch else None


def get_comment_project_id(s: Session, comment_id: int) -> int | None:
    c = s.get(Comment, comment_id)
    if not c:
        return None
    if c.task_id is not None:
        return get_task_project_id(s, c.task_id)
    if c.story_id is not None:
        return get_story_project_id(s, c.story_id)
    if c.epic_id is not None:
        return get_epic_project_id(s, c.epic_id)
    return None


def get_attachment_project_id(s: Session, attachment_id: int) -> int | None:
    a = s.get(Attachment, attachment_id)
    if not a:
        return None
    return get_task_project_id(s, a.task_id)


def get_dependency_project_id(s: Session, dependency_id: int) -> int | None:
    d = s.get(TaskDependency, dependency_id)
    if not d:
        return None
    return get_task_project_id(s, d.task_id)


def get_webhook_project_id(s: Session, webhook_id: int) -> int | None:
    wh = s.get(WebhookConfig, webhook_id)
    return wh.project_id if wh else None


# ---------- Notification ----------
def create_notification(
    s: Session, *, user_id: int, notif_type: str, title: str,
    content: str = "", link: str | None = None,
) -> Notification:
    if not s.get(models.User, user_id):
        raise NotFound(f"user {user_id} not found")
    valid_types = {
        "project_invite", "join_request", "task_assigned", "status_changed", "mentioned",
    }
    if notif_type not in valid_types:
        raise InvalidValue(f"notification type must be one of: {valid_types}")
    n = Notification(
        user_id=user_id, type=notif_type, title=title,
        content=content, link=link,
    )
    s.add(n); _commit(s); s.refresh(n); return n


def list_notifications(
    s: Session, user_id: int, limit: int | None = None, offset: int = 0,
    unread_only: bool = False,
) -> tuple[list, int]:
    q = s.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    total = q.count()
    return _paginate(q.order_by(Notification.created_at.desc()), limit, offset).all(), total


def search_notifications(s: Session, user_id: int, q: str, limit: int = 20):
    """当前用户通知关键词搜索（title/content），供命令面板等场景使用（v6.15）。

    通知属用户隐私数据，必须按 user_id 隔离，仅返回本人通知。
    """
    like = f"%{q}%"
    qry = (
        s.query(Notification)
        .filter(Notification.user_id == user_id,
                or_(Notification.title.ilike(like), Notification.content.ilike(like)))
        .order_by(Notification.created_at.desc())
    )
    return qry.limit(limit).all()


def mark_notification_read(s: Session, notif_id: int, user_id: int) -> Notification | None:
    n = s.get(Notification, notif_id)
    if not n or n.user_id != user_id:
        return None
    n.is_read = True; _commit(s); s.refresh(n); return n


def mark_all_notifications_read(s: Session, user_id: int) -> int:
    count = (
        s.query(Notification)
        .filter(Notification.user_id == user_id, Notification.is_read == False)
        .update({"is_read": True})
    )
    _commit(s); return count


def delete_notification(s: Session, notif_id: int, user_id: int) -> bool:
    n = s.get(Notification, notif_id)
    if not n or n.user_id != user_id:
        return False
    s.delete(n); _commit(s); return True


# ---------- Project statistics ----------
def get_project_stats(s: Session, project_id: int) -> dict:
    """返回项目统计：每日新增/开发/完成任务量（最近 30 天）

    优化：使用单个查询获取多个统计值，减少数据库往返次数
    """
    from datetime import timedelta, datetime as dt
    from sqlalchemy import func, case
    now = dt.now()
    thirty_days_ago = now - timedelta(days=30)

    # 使用条件聚合一次获取所有计数统计
    stats = (
        s.query(
            func.count(Task.id).label("total"),
            func.sum(case((Task.status == Status.DONE, 1), else_=0)).label("done"),
            func.sum(case((Task.status == "backlog", 1), else_=0)).label("backlog"),
            func.sum(case(
                (Task.status.in_(["in_progress", "in_review", "verifying"]), 1),
                else_=0
            )).label("active"),
        )
        .filter(Task.project_id == project_id)
        .first()
    )
    total_tasks = stats.total or 0
    done_tasks = stats.done or 0
    backlog_tasks = stats.backlog or 0
    active_tasks = stats.active or 0

    # 每日新建任务数
    daily_created = (
        s.query(
            func.date(Task.created_at).label("day"),
            func.count(Task.id).label("count"),
        )
        .filter(Task.project_id == project_id, Task.created_at >= thirty_days_ago)
        .group_by(func.date(Task.created_at))
        .order_by(func.date(Task.created_at))
        .all()
    )

    # 每日完成任务数（status 变为 done）
    daily_done = (
        s.query(
            func.date(Task.updated_at).label("day"),
            func.count(Task.id).label("count"),
        )
        .filter(
            Task.project_id == project_id,
            Task.status == Status.DONE,
            Task.updated_at >= thirty_days_ago,
        )
        .group_by(func.date(Task.updated_at))
        .order_by(func.date(Task.updated_at))
        .all()
    )

    return {
        "daily_created": [{"day": str(r.day), "count": r.count} for r in daily_created],
        "daily_done": [{"day": str(r.day), "count": r.count} for r in daily_done],
        "active_tasks": active_tasks,
        "backlog_tasks": backlog_tasks,
        "total_tasks": total_tasks,
        "done_tasks": done_tasks,
        "completion_rate": round(done_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0,
    }


# ---------- Admin: user management ----------
def list_users(s: Session, limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = s.query(models.User).order_by(models.User.id.desc())
    total = q.count()
    return _paginate(q, limit, offset).all(), total


def set_user_admin(s: Session, user_id: int, is_admin: bool) -> models.User | None:
    u = s.get(models.User, user_id)
    if not u:
        return None
    u.is_admin = is_admin; _commit(s); s.refresh(u); return u


def list_all_projects_admin(s: Session, limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    """管理员视角：所有项目（带成员数统计）"""
    q = s.query(Project).order_by(Project.id.desc())
    total = q.count()
    projects = _paginate(q, limit, offset).all()
    result = []
    for p in projects:
        row = _ser(p)
        row["member_count"] = (
            s.query(ProjectMember)
            .filter(ProjectMember.project_id == p.id)
            .count()
        ) or 0
        result.append(row)
    return result, total


# ---------- Visibility-filtered project list ----------
def list_accessible_projects(
    s: Session, user_id: int | None, limit: int | None = None, offset: int = 0,
) -> tuple[list, int]:
    """返回用户可见的项目列表。

    访问规则（2026-07-21 邀请制）：
    - 管理员：可见全部项目（``user.is_admin=True``）。
    - 普通用户：仅可见自己是成员的项目（邀请制）。
    - 未登录：空列表。

    ``abk_`` API Key 经 ``_current_user()`` 解析为关联用户的完整身份
    （含 ``is_admin``），因此权限与用户一致 —— 管理员 key 可见全部，
    普通用户 key 仅见成员项目。
    """
    if user_id is None:
        q = s.query(Project).filter(False)  # 未登录 → 空
        total = 0
        return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total

    user = s.get(User, user_id)
    if user and user.is_admin:
        # 管理员：全量
        q = s.query(Project)
    else:
        # 普通用户：仅成员项目
        member_project_ids = [
            r[0]
            for r in s.query(ProjectMember.project_id)
            .filter(ProjectMember.user_id == user_id)
            .all()
        ]
        if member_project_ids:
            q = s.query(Project).filter(Project.id.in_(member_project_ids))
        else:
            q = s.query(Project).filter(False)  # 无成员项目 → 空
    total = q.count()
    return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total


def list_user_projects(
    s: Session, user_id: int, *, role: str | None = None,
    limit: int | None = None, offset: int = 0,
) -> tuple[list[tuple[Project, str]], int]:
    q = (
        s.query(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .filter(ProjectMember.user_id == user_id)
    )
    if role is not None:
        if role not in {"owner", "member"}:
            raise InvalidValue("role must be 'owner' or 'member'")
        q = q.filter(ProjectMember.role == role)
    total = q.count()
    return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total


# ---------- Dashboard overview（跨项目聚合统计，首页性能优化） ----------
def get_overview(s: Session, user_id: int | None) -> dict:
    """跨项目聚合统计：首页 Dashboard 单请求数据源。

    可见性规则与 ``list_accessible_projects`` 一致：
    - 管理员：全部项目；
    - 普通用户：仅成员项目；
    - 未登录（user_id=None）：空。

    返回结构：
    {
      "counts": {"projects": N, "epics": N, "stories": N, "tasks": N, "done_tasks": N},
      "projects": [{"id", "name", "total", "done", "percent"}],   # 按 total 降序
      "status_distribution": [{"status", "count"}],                # 仅 count>0，按 ALL_STATUSES 顺序
      "activity_7d": [{"day", "count"}],                           # 近 7 天（含 0），按日升序
    }
    """
    from datetime import timedelta, datetime as dt
    from sqlalchemy import case

    projects, _ = list_accessible_projects(s, user_id)
    project_ids = [p.id for p in projects]
    if not project_ids:
        return {
            "counts": {"projects": 0, "epics": 0, "stories": 0, "tasks": 0, "done_tasks": 0},
            "projects": [],
            "status_distribution": [],
            "activity_7d": [],
        }

    epic_count = (
        s.query(func.count(Epic.id)).filter(Epic.project_id.in_(project_ids)).scalar() or 0
    )
    story_count = (
        s.query(func.count(Story.id))
        .join(Epic, Story.epic_id == Epic.id)
        .filter(Epic.project_id.in_(project_ids))
        .scalar() or 0
    )
    task_count = (
        s.query(func.count(Task.id)).filter(Task.project_id.in_(project_ids)).scalar() or 0
    )
    done_tasks = (
        s.query(func.count(Task.id))
        .filter(Task.project_id.in_(project_ids), Task.status == Status.DONE)
        .scalar() or 0
    )

    # 各项目任务进度（含 0 任务项目，按 total 降序）
    per_project = dict(
        s.query(Task.project_id, func.count(Task.id))
        .filter(Task.project_id.in_(project_ids))
        .group_by(Task.project_id)
        .all()
    )
    per_project_done = dict(
        s.query(Task.project_id, func.count(Task.id))
        .filter(Task.project_id.in_(project_ids), Task.status == Status.DONE)
        .group_by(Task.project_id)
        .all()
    )
    projects_out = []
    for p in projects:
        total = per_project.get(p.id, 0)
        done = per_project_done.get(p.id, 0)
        projects_out.append({
            "id": p.id,
            "name": p.name,
            "total": total,
            "done": done,
            "percent": round(done / total * 100) if total else 0,
        })
    projects_out.sort(key=lambda row: (-row["total"], row["id"]))

    # 状态分布（按 ALL_STATUSES 顺序，含 0）
    status_counts = dict(
        s.query(Task.status, func.count(Task.id))
        .filter(Task.project_id.in_(project_ids))
        .group_by(Task.status)
        .all()
    )
    status_distribution = [
        {"status": st, "count": status_counts.get(st, 0)} for st in ALL_STATUSES
    ]

    # 近 7 日活动（按 updated_at 日计数，含 0）
    now = dt.now()
    seven_days_ago = now - timedelta(days=6)
    day_counts = {
        str(day): count
        for day, count in (
            s.query(func.date(Task.updated_at).label("day"), func.count(Task.id))
            .filter(
                Task.project_id.in_(project_ids),
                Task.updated_at >= seven_days_ago,
            )
            .group_by(func.date(Task.updated_at))
            .all()
        )
    }
    activity_7d = [
        {"day": (seven_days_ago + timedelta(days=i)).date().isoformat(),
         "count": day_counts.get((seven_days_ago + timedelta(days=i)).date().isoformat(), 0)}
        for i in range(7)
    ]

    return {
        "counts": {
            "projects": len(project_ids),
            "epics": epic_count,
            "stories": story_count,
            "tasks": task_count,
            "done_tasks": done_tasks,
        },
        "projects": projects_out,
        "status_distribution": status_distribution,
        "activity_7d": activity_7d,
    }


# ---------- Epic 20: 批量操作 ----------
def batch_update_task_status(s: Session, task_ids: list[int], new_status: str,
                             *, changed_by: int | None = None) -> dict:
    """批量更新任务状态，返回成功和失败的任务ID列表。"""
    _check_status(new_status)
    new = Status(new_status)
    updated = []
    errors = []
    for tid in task_ids:
        t = s.get(Task, tid)
        if not t:
            errors.append({"id": tid, "error": f"task {tid} not found"})
            continue
        current = Status(t.status)
        if current != new:
            if new == Status.BLOCKED:
                ok = True  # blocked 全向可达
            elif current == Status.BLOCKED:
                prev = t.previous_status
                ok = (prev and Status(prev) == new) or new in transitions_for(
                    _task_needs_design(s, t)).get(Status.BLOCKED, set())
            else:
                ok = new in transitions_for(_task_needs_design(s, t)).get(current, set())
            if not ok:
                errors.append({"id": tid, "error": f"illegal transition {t.status} -> {new}"})
                continue
        old_status = t.status
        if old_status != str(new):
            t.status = new
            if new == Status.BLOCKED:
                t.previous_status = old_status
            elif old_status == Status.BLOCKED:
                t.previous_status = None
            _record_status_history(s, tid, old_status, str(new), changed_by=changed_by,
                                   reason="batch")
        updated.append(tid)
    _commit(s)
    return {"updated": updated, "errors": errors}


def batch_assign_sprint(s: Session, task_ids: list[int], sprint_id: int | None) -> dict:
    """批量分配 Sprint，支持将任务移入或移出 Sprint。"""
    updated = []
    errors = []
    sprint = None
    if sprint_id is not None:
        sprint = s.get(Sprint, sprint_id)
        if not sprint:
            raise InvalidValue(f"sprint {sprint_id} not found")
        if sprint.status == SprintStatus.COMPLETED:
            raise InvalidValue("cannot assign task to a completed sprint")
    for tid in task_ids:
        t = s.get(Task, tid)
        if not t:
            errors.append({"id": tid, "error": f"task {tid} not found"})
            continue
        if sprint and sprint.project_id != t.project_id:
            errors.append({"id": tid, "error": f"task {tid} does not belong to sprint's project"})
            continue
        t.sprint_id = sprint_id
        updated.append(tid)
    _commit(s)
    return {"updated": updated, "errors": errors}


def batch_delete_tasks(s: Session, task_ids: list[int]) -> dict:
    """批量删除任务，返回成功和失败的任务ID列表。"""
    deleted = []
    errors = []
    for tid in task_ids:
        t = s.get(Task, tid)
        if not t:
            errors.append({"id": tid, "error": f"task {tid} not found"})
            continue
        s.query(Comment).filter(Comment.task_id == tid).delete(synchronize_session=False)
        s.delete(t)
        deleted.append(tid)
    _commit(s)
    return {"deleted": deleted, "errors": errors}


# ---------- Epic 20: 增强搜索与排序 ----------
def search_tasks_enhanced(
    s: Session, *,
    project_id: int | None = None,
    epic_id: int | None = None,
    story_id: int | None = None,
    sprint_id: int | None = None,
    type: str | list[str] | None = None,
    status: str | list[str] | None = None,
    priority: str | list[str] | None = None,
    q: str | None = None,
    sort_by: str = "id",
    sort_order: str = "desc",
    limit: int | None = None,
    offset: int = 0,
):
    """增强搜索：支持多值过滤（status[], priority[]）和排序。"""
    qry = s.query(Task)
    if project_id is not None:
        qry = qry.filter(Task.project_id == project_id)
    if story_id is not None:
        qry = qry.filter(Task.story_id == story_id)
    if sprint_id is not None:
        qry = qry.filter(Task.sprint_id == sprint_id)
    if type is not None:
        if isinstance(type, list):
            qry = qry.filter(Task.type.in_(type))
        else:
            _check_type(type)
            qry = qry.filter(Task.type == type)
    if status is not None:
        if isinstance(status, list):
            for s_val in status:
                _check_status(s_val)
            qry = qry.filter(Task.status.in_(status))
        else:
            _check_status(status)
            qry = qry.filter(Task.status == status)
    if priority is not None:
        if isinstance(priority, list):
            for p_val in priority:
                _check_priority(p_val)
            qry = qry.filter(Task.priority.in_(priority))
        else:
            _check_priority(priority)
            qry = qry.filter(Task.priority == priority)
    if epic_id is not None:
        qry = qry.join(Story, Task.story_id == Story.id).filter(Story.epic_id == epic_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Task.title.ilike(like), Task.description.ilike(like),
                              Task.spec.ilike(like)))

    # 排序
    sort_col = {
        "id": Task.id, "created_at": Task.created_at, "updated_at": Task.updated_at,
        "priority": Task.priority, "status": Task.status, "title": Task.title,
    }.get(sort_by, Task.id)
    if sort_order.lower() == "asc":
        qry = qry.order_by(sort_col.asc())
    else:
        qry = qry.order_by(sort_col.desc())

    return _paginate(qry, limit, offset).all()


# ---------- Epic 20: 数据导出 ----------
def export_project_data(s: Session, project_id: int) -> dict:
    """导出项目完整数据（项目 + Epics + Stories + Tasks）。"""
    project = s.get(Project, project_id)
    if not project:
        raise NotFound(f"project {project_id} not found")

    # 获取所有 Epics
    epics = s.query(Epic).filter(Epic.project_id == project_id).all()
    epic_ids = [e.id for e in epics]

    # 获取所有 Stories
    stories = []
    story_ids = []
    if epic_ids:
        stories = s.query(Story).filter(Story.epic_id.in_(epic_ids)).all()
        story_ids = [st.id for st in stories]

    # 获取所有 Tasks
    task_filter = Task.project_id == project_id
    if story_ids:
        task_filter = or_(task_filter, Task.story_id.in_(story_ids))
    tasks = s.query(Task).filter(task_filter).all()

    return {
        "project": _ser(project),
        "epics": [_ser(e) for e in epics],
        "stories": [_ser(st) for st in stories],
        "tasks": [_ser(t) for t in tasks],
    }


def export_story_data(s: Session, story_id: int) -> dict:
    """导出 Story 及所有子任务数据。"""
    story = s.get(Story, story_id)
    if not story:
        raise NotFound(f"story {story_id} not found")

    tasks = s.query(Task).filter(Task.story_id == story_id).all()
    return {
        "story": _ser(story),
        "tasks": [_ser(t) for t in tasks],
    }


# ---------- Epic 22 Story 22.1: 审计日志 ----------
def create_audit_log(
    s: Session, *, user_id: int | None, action: str, entity_type: str,
    entity_id: int | None = None, method: str = "GET", path: str = "",
    ip_address: str | None = None, user_agent: str | None = None,
    request_body: str | None = None, response_status: int | None = None,
    duration_ms: int | None = None,
) -> AuditLog:
    """创建审计日志条目。"""
    log = AuditLog(
        user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id,
        method=method, path=path, ip_address=ip_address, user_agent=user_agent,
        request_body=request_body, response_status=response_status, duration_ms=duration_ms,
    )
    s.add(log)
    _commit(s)
    return log


def list_audit_logs(
    s: Session, *, project_id: int | None = None, entity_type: str | None = None,
    entity_id: int | None = None, user_id: int | None = None,
    action: str | None = None, limit: int | None = None, offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """查询审计日志列表。"""
    qry = s.query(AuditLog)
    if entity_type:
        qry = qry.filter(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        qry = qry.filter(AuditLog.entity_id == entity_id)
    if user_id is not None:
        qry = qry.filter(AuditLog.user_id == user_id)
    if action:
        qry = qry.filter(AuditLog.action == action)
    total = qry.count()
    qry = qry.order_by(AuditLog.created_at.desc())
    items = _paginate(qry, limit, offset).all()
    return items, total


# ---------- Epic 22 Story 22.2: 任务依赖关系 ----------
def add_task_dependency(
    s: Session, *, task_id: int, depends_on_id: int, dependency_type: str = "blocks",
) -> TaskDependency:
    """添加任务依赖关系。"""
    if task_id == depends_on_id:
        raise InvalidValue("task cannot depend on itself")
    # 检查是否已存在
    existing = s.query(TaskDependency).filter(
        TaskDependency.task_id == task_id,
        TaskDependency.depends_on_id == depends_on_id,
    ).first()
    if existing:
        raise Duplicate(f"dependency already exists")
    task = s.get(Task, task_id)
    dep_task = s.get(Task, depends_on_id)
    if not task:
        raise NotFound(f"task {task_id} not found")
    if not dep_task:
        raise NotFound(f"task {depends_on_id} not found")
    dep = TaskDependency(
        task_id=task_id, depends_on_id=depends_on_id, dependency_type=dependency_type,
    )
    s.add(dep)
    _commit(s)
    return dep


def remove_task_dependency(s: Session, dependency_id: int) -> None:
    """移除任务依赖关系。"""
    dep = s.get(TaskDependency, dependency_id)
    if not dep:
        raise NotFound(f"dependency {dependency_id} not found")
    s.delete(dep)
    _commit(s)


def get_task_dependencies(s: Session, task_id: int) -> dict:
    """获取任务的所有依赖关系。"""
    deps = s.query(TaskDependency).filter(TaskDependency.task_id == task_id).all()
    blockers = [
        {"id": d.id, "task_id": d.depends_on_id, "type": d.dependency_type,
         "task": _ser(s.get(Task, d.depends_on_id)) if s.get(Task, d.depends_on_id) else None}
        for d in deps
    ]
    # 反向依赖：该任务被谁阻塞
    blocked_by = s.query(TaskDependency).filter(TaskDependency.depends_on_id == task_id).all()
    blocking = [
        {"id": d.id, "task_id": d.task_id, "type": d.dependency_type,
         "task": _ser(s.get(Task, d.task_id)) if s.get(Task, d.task_id) else None}
        for d in blocked_by
    ]
    return {"blockers": blockers, "blocked_by": blocking}


# ---------- Epic 22 Story 22.4: Webhook 配置 ----------
def create_webhook(
    s: Session, *, project_id: int | None, name: str, url: str,
    secret: str | None = None, events: list[str] | None = None,
    created_by: int | None = None,
) -> WebhookConfig:
    """创建 Webhook 配置。"""
    import json
    name = _required(name, "name", 100)
    url_val = _required(url, "url", 2000)
    if not url_val.startswith(("http://", "https://")):
        raise InvalidValue("url must start with http:// or https://")
    wh = WebhookConfig(
        project_id=project_id, name=name, url=url_val, secret=secret or None,
        events=json.dumps(events or []), created_by=created_by,
    )
    s.add(wh)
    _commit(s)
    s.refresh(wh)
    return wh


def list_webhooks(s: Session, *, project_id: int | None = None) -> list[WebhookConfig]:
    """列出 Webhook 配置。"""
    qry = s.query(WebhookConfig)
    if project_id is not None:
        qry = qry.filter(WebhookConfig.project_id == project_id)
    return qry.order_by(WebhookConfig.created_at.desc()).all()


def delete_webhook(s: Session, webhook_id: int) -> None:
    """删除 Webhook 配置。"""
    wh = s.get(WebhookConfig, webhook_id)
    if not wh:
        raise NotFound(f"webhook {webhook_id} not found")
    s.delete(wh)
    _commit(s)


def toggle_webhook(s: Session, webhook_id: int, enabled: bool) -> WebhookConfig:
    """启用/停用 Webhook。"""
    wh = s.get(WebhookConfig, webhook_id)
    if not wh:
        raise NotFound(f"webhook {webhook_id} not found")
    wh.enabled = enabled
    _commit(s)
    return wh


def fire_webhook(webhook: WebhookConfig, event: str, payload: dict) -> bool:
    """触发 Webhook（异步发送 HTTP POST）。调用方需自行处理异常。"""
    import hashlib, hmac, json, time
    import httpx
    headers = {"Content-Type": "application/json", "User-Agent": "AgentBoard-Webhook/1.0"}
    if webhook.secret:
        timestamp = str(int(time.time()))
        body = json.dumps({"event": event, "timestamp": timestamp, "data": payload})
        signature = hmac.new(
            webhook.secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-AgentBoard-Signature"] = signature
        headers["X-AgentBoard-Timestamp"] = timestamp
    else:
        body = json.dumps({"event": event, "data": payload})
    try:
        resp = httpx.post(webhook.url, content=body, headers=headers, timeout=10.0)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


def fire_webhooks_for_event(s: Session, *, project_id: int, event: str,
                            payload: dict | None = None) -> dict:
    """按事件向项目下的 Webhook 配置派发（Epic 122 切片 3 M1）。

    过滤语义：
    - 仅派发 ``enabled=True`` 的 WebhookConfig；
    - ``events`` 配置（JSON 数组）为空列表 → 订阅全部事件；非空 → 精确包含
      ``event`` 才派发（与 RabbitMQ workflow 事件名同构，见 mq.EVENT_*）；
    - 单 Webhook 失败（网络异常 / 非 2xx）隔离，不影响其它 Webhook 派发。

    Webhook 事件只携带定位信息（实体 id / status / ref），状态一律以 DB 为准，
    与 workflow 事件总线铁律一致。本函数不抛异常（best-effort），返回统计：:

        {"matched": 命中并尝试派发的 webhook 数, "succeeded": 2xx 成功的 webhook 数}

    注意：HTTP 派发是同步的（单发超时 10s）。调用方若在请求路径上，应评估
    Webhook 数量与耗时；MVP 量级（项目级 webhook 通常个位数）可接受。
    """
    import json
    if payload is None:
        payload = {}
    matched = succeeded = 0
    try:
        rows = s.query(WebhookConfig).filter(
            or_(WebhookConfig.project_id == project_id,
                WebhookConfig.project_id.is_(None)),  # 项目级 + 全局（project_id=NULL）
            WebhookConfig.enabled.is_(True),
        ).all()
    except Exception:
        # DB 异常不阻断主业务（best-effort）
        return {"matched": 0, "succeeded": 0}
    for wh in rows:
        try:
            subscribed = json.loads(wh.events or "[]")
        except (TypeError, ValueError):
            subscribed = []
        if subscribed and event not in subscribed:
            continue  # 空列表 = 订阅全部；非空需精确匹配
        matched += 1
        try:
            if fire_webhook(wh, event, payload):
                succeeded += 1
        except Exception:
            # 单 webhook 异常隔离：不影响其它 webhook 派发
            log.warning("webhook %s（%s）派发 %s 失败：%s",
                        wh.id, wh.name, event, traceback.format_exc(limit=2))
    return {"matched": matched, "succeeded": succeeded}


# ---------- Epic 22 Story 22.3: 数据导入 ----------
def import_tasks_from_json(s: Session, project_id: int, data: dict) -> dict:
    """从 JSON 数据导入任务。"""
    import json
    imported = []
    errors = []
    tasks_data = data.get("tasks", [])
    for item in tasks_data:
        try:
            title = _required(item.get("title", "").strip(), "title", 300)
            task = Task(
                project_id=project_id,
                title=title,
                type=item.get("type", "task"),
                description=item.get("description", ""),
                priority=item.get("priority", "medium"),
                status=item.get("status", "backlog"),
            )
            s.add(task)
            s.flush()
            imported.append({"id": task.id, "title": task.title})
        except Exception as e:
            errors.append({"title": item.get("title", "?"), "error": str(e)})
    _commit(s)
    return {"imported": imported, "errors": errors}


# ---------- Documents (Epic 15：项目文档维护 / 多成员·多 Agent 协作) ----------
def _check_document_type(value: str) -> None:
    if value not in ALL_DOCUMENT_TYPES:
        raise InvalidValue(f"invalid document type '{value}'")


def _check_document_status(value: str) -> None:
    if value not in ALL_DOCUMENT_STATUSES:
        raise InvalidValue(f"invalid document status '{value}'")


def _check_document_folder(s: Session, folder_id: int, project_id: int) -> DocumentFolder:
    """校验文件夹存在且属于指定项目；通过则返回该文件夹，否则抛 InvalidValue。"""
    f = s.get(DocumentFolder, folder_id)
    if not f:
        raise InvalidValue(f"folder {folder_id} not found")
    if f.project_id != project_id:
        raise InvalidValue("folder does not belong to the document's project")
    return f


def _folder_is_descendant(s: Session, folder_id: int, ancestor_id: int) -> bool:
    """ancestor_id 是否为 folder_id 的祖先（含自身）？用于移动文件夹时防环。"""
    cur: int | None = ancestor_id
    seen: set[int] = set()
    while cur is not None:
        if cur == folder_id:
            return True
        if cur in seen:
            return False
        seen.add(cur)
        f = s.get(DocumentFolder, cur)
        cur = f.parent_id if f else None
    return False


def create_document_folder(
    s: Session, *, project_id: int, name: str, parent_id: int | None = None,
) -> DocumentFolder:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    name = _required(name, "name", 300)
    if parent_id is not None:
        _check_document_folder(s, parent_id, project_id)
    f = DocumentFolder(project_id=project_id, parent_id=parent_id, name=name)
    s.add(f); _commit(s); s.refresh(f); return f


def list_document_folders(
    s: Session, *, project_id: int | None = None, user_id: int | None = None,
):
    """列出文件夹（含所有层级，由前端组装树）。

    权限口径与 list_documents 一致：指定 project_id 时按项目过滤；
    未指定但带用户身份时，仅返回该用户有权限项目的文件夹。
    """
    qry = s.query(DocumentFolder)
    if project_id is not None:
        qry = qry.filter(DocumentFolder.project_id == project_id)
    elif user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(DocumentFolder.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    return qry.order_by(DocumentFolder.name, DocumentFolder.id).all()


def update_document_folder(
    s: Session, id: int, **fields,
) -> DocumentFolder | None:
    f = s.get(DocumentFolder, id)
    if not f:
        return None
    if "name" in fields and fields["name"] is not None:
        f.name = _required(fields["name"], "name", 300)
    if "parent_id" in fields:
        new_parent = fields["parent_id"]
        if new_parent is not None:
            _check_document_folder(s, new_parent, f.project_id)
            if _folder_is_descendant(s, id, new_parent):
                raise InvalidValue("cannot move a folder into itself or its descendant")
        f.parent_id = new_parent
    _commit(s); s.refresh(f); return f


def delete_document_folder(s: Session, id: int) -> bool:
    """删除文件夹：直接子文档与子文件夹上提至被删文件夹的父级（根则置 NULL）。

    不级联删除子项，避免用户误删文件夹时连带丢失文档。
    """
    f = s.get(DocumentFolder, id)
    if not f:
        return False
    parent_id = f.parent_id
    s.query(Document).filter(Document.folder_id == id).update(
        {Document.folder_id: parent_id}, synchronize_session=False,
    )
    s.query(DocumentFolder).filter(DocumentFolder.parent_id == id).update(
        {DocumentFolder.parent_id: parent_id}, synchronize_session=False,
    )
    s.delete(f); _commit(s); return True


def get_document_folder_project_id(s: Session, folder_id: int) -> int | None:
    f = s.get(DocumentFolder, folder_id)
    return f.project_id if f else None


def create_document(
    s: Session, *, project_id: int, title: str, content: str = "",
    type: str = "plan", status: str = "draft",
    epic_id: int | None = None, story_id: int | None = None,
    folder_id: int | None = None, author_id: int | None = None,
) -> Document:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    if epic_id is not None and not s.get(Epic, epic_id):
        raise NotFound(f"epic {epic_id} not found")
    if story_id is not None and not s.get(Story, story_id):
        raise NotFound(f"story {story_id} not found")
    if folder_id is not None:
        _check_document_folder(s, folder_id, project_id)
    _check_document_type(type)
    _check_document_status(status)
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    doc = Document(
        project_id=project_id, epic_id=epic_id, story_id=story_id,
        title=_required(title, "title", 300), content=content or "",
        type=type, status=status, folder_id=folder_id, author_id=author_id,
    )
    s.add(doc); _commit(s); s.refresh(doc); return doc


def get_document(s: Session, id: int) -> Document | None:
    return s.get(Document, id)


def list_documents(
    s: Session, *, project_id: int | None = None, type: str | None = None,
    status: str | None = None, q: str | None = None,
    limit: int | None = None, offset: int = 0, user_id: int | None = None,
):
    qry = s.query(Document)
    if project_id is not None:
        qry = qry.filter(Document.project_id == project_id)
    elif user_id is not None:
        # 未指定 project_id 但有用户身份：仅返回该用户有权限的项目文档
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(Document.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)  # 非 admin 无成员项目 → 空
    if type is not None:
        _check_document_type(type)
        qry = qry.filter(Document.type == type)
    if status is not None:
        _check_document_status(status)
        qry = qry.filter(Document.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Document.title.ilike(like), Document.content.ilike(like)))
    qry = qry.order_by(Document.updated_at.desc(), Document.id.desc())
    return _paginate(qry, limit, offset).all()


def update_document(s: Session, id: int, **fields) -> Document | None:
    d = s.get(Document, id)
    if not d:
        return None
    allowed = {"title", "content", "type", "status", "folder_id"}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "folder_id":
            # 显式 null = 移出文件夹到根目录（合法值，不可跳过）
            if v is not None:
                _check_document_folder(s, v, d.project_id)
            d.folder_id = v
            continue
        if v is None:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "type":
            _check_document_type(v)
        elif k == "status":
            _check_document_status(v)
            new = DocumentStatus(v)
            current = DocumentStatus(d.status)
            if current != new and new not in DOCUMENT_TRANSITIONS.get(current, set()):
                raise IllegalTransition(f"{d.status} -> {new.value} 不合法")
            d.status = new.value
            status_changed = True
            continue
        setattr(d, k, v)
    _commit(s); s.refresh(d); return d


def delete_document(s: Session, id: int) -> bool:
    d = s.get(Document, id)
    if not d:
        return False
    # 级联删除评论（外键 ondelete=CASCADE 也会兜底）
    s.query(DocumentComment).filter(DocumentComment.document_id == id).delete(synchronize_session=False)
    s.delete(d); _commit(s); return True


def set_document_status(s: Session, id: int, new_status: str) -> Document | None:
    d = s.get(Document, id)
    if not d:
        raise NotFound(f"document {id} not found")
    _check_document_status(new_status)
    new = DocumentStatus(new_status)
    current = DocumentStatus(d.status)
    if current != new and new not in DOCUMENT_TRANSITIONS.get(current, set()):
        raise IllegalTransition(f"{d.status} -> {new} 不合法")
    d.status = new
    _commit(s); s.refresh(d); return d


def create_document_comment(
    s: Session, *, document_id: int, author: str, content: str,
    author_id: int | None = None,
) -> DocumentComment:
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    author = (author or "").strip()
    content = (content or "").strip()
    if not author or not content:
        raise InvalidValue("author and content are required")
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    c = DocumentComment(
        document_id=document_id, author=author[:100], content=content, author_id=author_id,
    )
    s.add(c); _commit(s); s.refresh(c); return c


def list_document_comments(s: Session, document_id: int):
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    return (
        s.query(DocumentComment)
        .filter(DocumentComment.document_id == document_id)
        .order_by(DocumentComment.created_at, DocumentComment.id)
        .all()
    )


def update_document_comment(
    s: Session, id: int, content: str, *, author: str,
) -> DocumentComment | None:
    """编辑文档评论：仅作者（成员或 Agent 账号）可编辑自己的评论。"""
    c = s.get(DocumentComment, id)
    if not c:
        return None
    content = (content or "").strip()
    if not content:
        raise InvalidValue("content is required")
    if c.author != (author or "").strip():
        raise InvalidValue("only the author can edit this comment")
    c.content = content
    _commit(s); s.refresh(c); return c


def delete_document_comment(s: Session, id: int) -> bool:
    c = s.get(DocumentComment, id)
    if not c:
        return False
    s.delete(c); _commit(s); return True


def get_document_project_id(s: Session, document_id: int) -> int | None:
    d = s.get(Document, document_id)
    return d.project_id if d else None


def get_document_comment_project_id(s: Session, comment_id: int) -> int | None:
    c = s.get(DocumentComment, comment_id)
    if not c:
        return None
    d = s.get(Document, c.document_id)
    return d.project_id if d else None


# ---------- Proposals (Epic 96 P0：Proposal 澄清回路 / 人机协同需求分析) ----------
def _check_proposal_status(value: str) -> None:
    if value not in ALL_PROPOSAL_STATUSES:
        raise InvalidValue(f"invalid proposal status '{value}'")


def _proposal_or_404(s: Session, proposal_id: int) -> Proposal:
    p = s.get(Proposal, proposal_id)
    if not p:
        raise NotFound(f"proposal {proposal_id} not found")
    return p


def create_proposal(
    s: Session, *, project_id: int, title: str, content: str = "",
    author_id: int | None = None,
) -> Proposal:
    """新建需求提案，初始状态 draft、current_round=0。"""
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    p = Proposal(
        project_id=project_id,
        title=_required(title, "title", 300),
        content=content or "",
        status=ProposalStatus.DRAFT.value,
        current_round=0,
        author_id=author_id,
    )
    s.add(p); _commit(s); s.refresh(p); return p


def get_proposal(s: Session, id: int) -> Proposal | None:
    return s.get(Proposal, id)


def list_proposals(
    s: Session, *, project_id: int | None = None, status: str | None = None,
    q: str | None = None, limit: int | None = None, offset: int = 0,
    user_id: int | None = None,
):
    """列出提案。未指定 project_id 时按调用者可见项目收敛（与文档模块一致）。"""
    qry = s.query(Proposal)
    if project_id is not None:
        qry = qry.filter(Proposal.project_id == project_id)
    elif user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(Proposal.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    if status is not None:
        _check_proposal_status(status)
        qry = qry.filter(Proposal.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Proposal.title.ilike(like), Proposal.content.ilike(like)))
    qry = qry.order_by(Proposal.updated_at.desc(), Proposal.id.desc())
    return _paginate(qry, limit, offset).all()


def update_proposal(s: Session, id: int, **fields) -> Proposal | None:
    """编辑提案正文（状态流转请用 set_proposal_status）。"""
    p = s.get(Proposal, id)
    if not p:
        return None
    allowed = {"title", "content", "converged_spec", "story_id"}
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "story_id" and not s.get(Story, v):
            raise NotFound(f"story {v} not found")
        setattr(p, k, v)
    _commit(s); s.refresh(p); return p


def delete_proposal(s: Session, id: int) -> bool:
    p = s.get(Proposal, id)
    if not p:
        return False
    # 显式清理子表（外键 ondelete=CASCADE 也会兜底；SQLite 默认不强制外键）
    s.query(ProposalQuestion).filter(ProposalQuestion.proposal_id == id).delete(
        synchronize_session=False,
    )
    s.query(ProposalRound).filter(ProposalRound.proposal_id == id).delete(
        synchronize_session=False,
    )
    s.delete(p); _commit(s); return True


def set_proposal_status(
    s: Session, id: int, new_status: str, *, error: str | None = None,
) -> Proposal:
    """澄清状态机流转，非法迁移抛 IllegalTransition。"""
    p = _proposal_or_404(s, id)
    _check_proposal_status(new_status)
    new = ProposalStatus(new_status)
    current = ProposalStatus(p.status)
    if current != new and new not in PROPOSAL_TRANSITIONS.get(current, set()):
        raise IllegalTransition(f"{p.status} -> {new.value} 不合法")
    p.status = new.value
    if new is ProposalStatus.FAILED:
        p.error = error or p.error or "unspecified failure"
    elif error is None and new is not ProposalStatus.FAILED:
        p.error = ""
    # 租约随状态同步维护，避免脏租约：
    # - 进入 analyzing（含旧版 PUT /status 认领路径）：盖上租约时间戳，
    #   否则这类行的 claimed_at 恒为 NULL，崩溃后永远不会被回收。
    # - 离开 analyzing：清空租约，防止已收敛/失败的提案仍挂着持有者。
    if new is ProposalStatus.ANALYZING:
        p.claimed_at = utc_now()
    else:
        p.claimed_by = ""
        p.claimed_at = None
    _commit(s); s.refresh(p); return p


# Worker 认领租约默认时长（秒）；与 worker.py AGENTBOARD_WORKER_LEASE 默认值一致。
DEFAULT_CLAIM_LEASE_SECONDS = 1800


def claim_proposal(s: Session, id: int, *, agent: str = "") -> Proposal | None:
    """**原子**认领提案：queued/answered → analyzing。

    返回 Proposal 表示认领成功；返回 ``None`` 表示竞争失败（已被他人持有或状态
    不可认领）；提案不存在抛 NotFound。

    为什么必须是单条条件 UPDATE，而不能「先 GET 复核状态再 PUT」：
    ``set_proposal_status`` 对同状态迁移（analyzing→analyzing）是幂等 no-op，
    直接返回 200 而非 400 —— 也就是说 PUT 本身**不具备仲裁能力**，N 个 Worker
    会全部拿到 200 并同时开工。加一次前置 GET 只能收窄窗口，读与写之间的
    TOCTOU 依然存在。这里把「判定」和「写入」压进同一条 SQL：

        UPDATE proposals SET status='analyzing', ...
         WHERE id=:id AND status IN ('queued','answered')

    单行条件更新的原子性由存储引擎保证（SQLite 写锁全局串行化；MariaDB/InnoDB
    行级排他锁 + 加锁读），后到者必然读到已提交的 'analyzing' 而匹配不到任何行，
    ``rowcount`` 恰为 0。竞争由数据库仲裁，不留任何窗口。

    注意语句顺序：UPDATE 必须是本会话的**第一条** SQL。若先 SELECT 会先取到读锁
    再升级为写锁，在并发下平白增加锁冲突（WAL 模式下还可能触发 BUSY_SNAPSHOT）。
    因此「区分 404 与竞争失败」的查询放在 rowcount=0 之后才执行。
    """
    now = utc_now()
    claimable = sorted(st.value for st in CLAIMABLE_STATUSES)
    res = s.execute(
        update(Proposal)
        .where(Proposal.id == id, Proposal.status.in_(claimable))
        .values(
            status=ProposalStatus.ANALYZING.value,
            claimed_by=(agent or "").strip()[:100],
            claimed_at=now,
            updated_at=now,
            error="",
        )
        .execution_options(synchronize_session=False),
    )
    if res.rowcount == 1:
        _commit(s)
        p = s.get(Proposal, id)
        s.refresh(p)
        return p
    # 一行都没命中：可能是提案不存在，也可能是竞争失败 —— 此时才去读一次区分。
    s.rollback()
    if not s.get(Proposal, id):
        raise NotFound(f"proposal {id} not found")
    return None


def reclaim_stale_proposals(
    s: Session, *, lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
) -> list[int]:
    """把租约过期的 analyzing 提案批量回退 queued，返回被回收的 id 列表。

    这是整个自动化闭环唯一的丢单兜底：持有者进程被 kill 后，提案必须能重新入队。

    判定依据是 ``claimed_at`` 而非 ``updated_at``：后者带 onupdate，用户作答、
    PATCH converged_spec 等**与持有者无关**的写入都会刷新它，导致一个早已崩溃的
    Worker 的租约被旁人不断续期，提案永久卡死在 analyzing。
    """
    if lease_seconds < 0:
        raise InvalidValue("lease_seconds must be >= 0")
    now = utc_now()
    cutoff = now - timedelta(seconds=lease_seconds)
    analyzing = ProposalStatus.ANALYZING.value
    # claimed_at 为 NULL 的 analyzing 行只可能来自本迁移之前（历史遗留），
    # 对它们退回用 updated_at 兜底，避免升级后这批行永远无法回收。
    stale = or_(
        Proposal.claimed_at < cutoff,
        and_(Proposal.claimed_at.is_(None), Proposal.updated_at < cutoff),
    )
    ids = [
        row[0]
        for row in s.query(Proposal.id)
        .filter(Proposal.status == analyzing, stale)
        .all()
    ]
    if not ids:
        return []
    s.execute(
        update(Proposal)
        .where(Proposal.id.in_(ids), Proposal.status == analyzing)
        .values(
            status=ProposalStatus.QUEUED.value,
            claimed_by="",
            claimed_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    _commit(s)
    s.expire_all()
    return ids


def create_proposal_round(
    s: Session, *, proposal_id: int, round_no: int | None = None,
    summary: str = "", agent: str = "",
) -> ProposalRound:
    """开启一轮澄清。

    ``(proposal_id, round_no)`` 唯一：消息 at-least-once 重投时，同一轮次重复创建
    会命中唯一约束并直接复用既有轮次，天然幂等。

    注意状态校验与幂等复用的**先后顺序**：一次成功提问会把提案推进到 awaiting，
    若先校验状态再查重，任何重投都会在 awaiting 上被 IllegalTransition 挡下，
    「幂等复用」分支永远走不到——即 at-least-once 保证形同虚设。
    因此这里的规则是：**新开轮次**需要 analyzing；**重投已存在的轮次**在任何
    状态下都安全返回既有轮次（不改动任何数据）。
    """
    p = _proposal_or_404(s, proposal_id)
    askable = ProposalStatus(p.status) in ASKABLE_STATUSES
    if round_no is None:
        # 未显式指定轮次时无法判定是否重投，只能按「新开一轮」处理
        if not askable:
            raise IllegalTransition(
                f"proposal {proposal_id} 当前状态 {p.status}，仅 analyzing 可提问",
            )
        round_no = (p.current_round or 0) + 1
    if round_no < 1:
        raise InvalidValue("round_no must be >= 1")
    existing = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id,
                ProposalRound.round_no == round_no)
        .first()
    )
    if existing:  # 幂等：重投同一轮次直接复用（任何状态下都不产生副作用）
        return existing
    if not askable:
        raise IllegalTransition(
            f"proposal {proposal_id} 当前状态 {p.status}，仅 analyzing 可提问",
        )
    r = ProposalRound(
        proposal_id=proposal_id, round_no=round_no,
        summary=summary or "", agent=(agent or "")[:100],
    )
    s.add(r)
    p.current_round = max(p.current_round or 0, round_no)
    _commit(s); s.refresh(r); return r


def add_proposal_questions(
    s: Session, *, proposal_id: int, questions: list[str],
    round_no: int | None = None, summary: str = "", agent: str = "",
) -> dict:
    """Agent 回写一轮 open questions，并把提案推进到 awaiting。

    返回 ``{"round": <round dict>, "questions": [...]}``。
    """
    if not questions:
        raise InvalidValue("questions must not be empty")
    cleaned = [str(q).strip() for q in questions if str(q).strip()]
    if not cleaned:
        raise InvalidValue("questions must not be empty")
    r = create_proposal_round(
        s, proposal_id=proposal_id, round_no=round_no, summary=summary, agent=agent,
    )
    # 幂等：同一轮次已有问题则不再重复写入
    already = (
        s.query(ProposalQuestion).filter(ProposalQuestion.round_id == r.id).count()
    )
    if already == 0:
        for i, text in enumerate(cleaned, start=1):
            s.add(ProposalQuestion(
                proposal_id=proposal_id, round_id=r.id, seq=i, question=text,
            ))
        _commit(s)
    p = _proposal_or_404(s, proposal_id)
    if ProposalStatus(p.status) is ProposalStatus.ANALYZING:
        p.status = ProposalStatus.AWAITING.value
        _commit(s); s.refresh(p)
    rows = (
        s.query(ProposalQuestion)
        .filter(ProposalQuestion.round_id == r.id)
        .order_by(ProposalQuestion.seq.asc(), ProposalQuestion.id.asc())
        .all()
    )
    return {"round": _ser(r), "questions": [_ser(x) for x in rows]}


def answer_proposal_question(
    s: Session, question_id: int, *, answer: str = "", unsure: bool = False,
    user_id: int | None = None,
) -> ProposalQuestion:
    """用户作答单条问题；``unsure=True`` 表示标记不确定（视为已处理）。"""
    qs = s.get(ProposalQuestion, question_id)
    if not qs:
        raise NotFound(f"proposal question {question_id} not found")
    answer = (answer or "").strip()
    if not answer and not unsure:
        raise InvalidValue("answer is required unless marked unsure")
    if user_id is not None and not s.get(User, user_id):
        raise InvalidValue(f"user {user_id} not found")
    qs.answer = answer
    qs.unsure = bool(unsure)
    qs.answered_at = utc_now()
    qs.answered_by = user_id
    _commit(s); s.refresh(qs)
    _maybe_mark_answered(s, qs.proposal_id)
    return qs


def _maybe_mark_answered(s: Session, proposal_id: int) -> None:
    """当前轮次问题全部处理完毕时，自动把 awaiting 推进到 answered。"""
    p = s.get(Proposal, proposal_id)
    if not p or ProposalStatus(p.status) is not ProposalStatus.AWAITING:
        return
    r = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id,
                ProposalRound.round_no == p.current_round)
        .first()
    )
    if not r:
        return
    pending = (
        s.query(ProposalQuestion)
        .filter(ProposalQuestion.round_id == r.id,
                ProposalQuestion.answered_at.is_(None))
        .count()
    )
    if pending == 0:
        p.status = ProposalStatus.ANSWERED.value
        _commit(s)


def list_proposal_rounds(s: Session, proposal_id: int) -> list[dict]:
    """按轮次正序返回澄清历史（含每轮问题），供前端问答工作台渲染。"""
    _proposal_or_404(s, proposal_id)
    rounds = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id)
        .order_by(ProposalRound.round_no.asc())
        .all()
    )
    out = []
    for r in rounds:
        qs = (
            s.query(ProposalQuestion)
            .filter(ProposalQuestion.round_id == r.id)
            .order_by(ProposalQuestion.seq.asc(), ProposalQuestion.id.asc())
            .all()
        )
        item = _ser(r)
        item["questions"] = [_ser(x) for x in qs]
        out.append(item)
    return out


def get_proposal_project_id(s: Session, proposal_id: int) -> int | None:
    p = s.get(Proposal, proposal_id)
    return p.project_id if p else None


# P3：converged_spec 中生成子 Task 的清单项前缀（与 generate_tasks_from_spec 一致）
_SPEC_TASK_RE = re.compile(r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)")


def convert_proposal_to_story(
    s: Session, proposal_id: int, *, epic_id: int, title: str | None = None,
) -> tuple[Story, list[Task], Proposal]:
    """人工终审确认后，把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    - 要求提案状态为 converged，且 converged_spec 非空（否则 400/422 拒绝）；
    - 要求目标 Epic 存在且属于提案所在项目；
    - Story 标题 = 显式 title 或提案标题，description = converged_spec 原文；
    - 解析 converged_spec 中的 ``- [ ]`` 清单项生成子 Task
      （同 project/story，type=task，status=backlog，priority=medium）；
    - 回填 proposal.story_id 并推进 converged → story_created；
    - **幂等防重放**：story_id 已回填且 Story 仍存在时直接返回既有结果，
      不重复创建（呼应 P1 全量重放 / P2 at-least-once 的既有兜底策略）。

    返回 ``(story, tasks, proposal)``。
    """
    p = _proposal_or_404(s, proposal_id)

    # 幂等：已转化过且 Story 还在 → 直接复用，避免重放产生重复 Story。
    if p.story_id is not None:
        existing = s.get(Story, p.story_id)
        if existing is not None:
            tasks = (
                s.query(Task).filter(Task.story_id == existing.id).all()
            )
            return existing, tasks, p

    if ProposalStatus(p.status) is not ProposalStatus.CONVERGED:
        raise InvalidValue(
            f"proposal {proposal_id} 当前状态为 {p.status}，仅 converged 可转化为 Story",
        )
    if not (p.converged_spec or "").strip():
        raise InvalidValue(
            f"proposal {proposal_id} 的 converged_spec 为空，无法生成 Story",
        )

    epic = s.get(Epic, epic_id)
    if epic is None:
        raise NotFound(f"epic {epic_id} not found")
    if epic.project_id != p.project_id:
        raise InvalidValue(
            f"epic {epic_id} 不属于提案所在项目 {p.project_id}",
        )

    story = create_story(
        s, epic_id=epic_id,
        title=_required(title or p.title, "title", 300),
        description=p.converged_spec,
    )
    created: list[Task] = []
    seen: set[str] = set()
    for line in (p.converged_spec or "").splitlines():
        m = _SPEC_TASK_RE.match(line)
        if not m:
            continue
        t_title = m.group(1).strip()
        if not t_title or t_title in seen:
            continue
        seen.add(t_title)
        created.append(
            create_task(
                s, project_id=p.project_id, story_id=story.id,
                title=t_title[:300], description=t_title,
                priority=Priority.MEDIUM,
            )
        )

    p.story_id = story.id
    # converged → story_created（终态）；直接改状态字段，不经 set_proposal_status
    # 的租约维护逻辑（这里不涉及 analyzing，无租约可清理）。
    p.status = ProposalStatus.STORY_CREATED.value
    p.error = ""
    _commit(s)
    s.refresh(story)
    s.refresh(p)
    for t in created:
        s.refresh(t)
    return story, created, p

