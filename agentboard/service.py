import re
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import models, auth
from .models import (
    ItemType, Status, Priority, SprintStatus, ALL_TYPES, ALL_STATUSES,
    ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES,
    Project, Epic, Story, Task, Comment, Sprint, Attachment, AgentSchedule, AgentRun,
    ProjectMember, Notification, User, ApiKey, AuditLog, TaskDependency, WebhookConfig,
)

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200

# 合法状态迁移
TRANSITIONS = {
    Status.BACKLOG: {Status.TODO},
    Status.TODO: {Status.IN_PROGRESS, Status.BACKLOG},
    Status.IN_PROGRESS: {Status.IN_REVIEW, Status.VERIFYING, Status.TODO},
    Status.IN_REVIEW: {Status.DONE, Status.IN_PROGRESS},
    Status.VERIFYING: {Status.DONE, Status.IN_PROGRESS},
    Status.DONE: {Status.IN_PROGRESS},
}

EDITABLE = {
    "name", "key", "description", "is_private",   # project
    "title", "description", "status",      # epic / story / task
    "type", "spec", "priority", "sprint_id",  # task
    # Epic 17: 任务管理增强
    "assignee_id", "due_date", "labels",
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
def create_project(s: Session, *, name: str, key=None, description: str = "") -> Project:
    name = _required(name, "name", 200)
    key = (key or "").strip() or None
    if key and len(key) > 20:
        raise InvalidValue("key must be at most 20 characters")
    p = Project(name=name, key=key, description=description or "")
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
    epic_ids = [x[0] for x in s.query(Epic.id).filter(Epic.project_id == id).all()]
    story_ids = []
    if epic_ids:
        story_ids = [x[0] for x in s.query(Story.id).filter(Story.epic_id.in_(epic_ids)).all()]
    task_filter = Task.project_id == id
    if story_ids:
        task_filter = or_(task_filter, Task.story_id.in_(story_ids))
    task_ids = [x[0] for x in s.query(Task.id).filter(task_filter).all()]
    if task_ids:
        s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
        s.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session=False)
    if story_ids:
        s.query(Story).filter(Story.id.in_(story_ids)).delete(synchronize_session=False)
    s.query(Epic).filter(Epic.project_id == id).delete()
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
    s.query(Story).filter(Story.epic_id == id).delete()
    s.delete(ep); _commit(s); return True


# ---------- Story ----------
def create_story(s: Session, *, epic_id: int, title: str, description: str = "") -> Story:
    if not s.get(Epic, epic_id):
        raise NotFound(f"epic {epic_id} not found")
    st = Story(epic_id=epic_id, title=_required(title, "title", 300), description=description or "")
    s.add(st); _commit(s); s.refresh(st); return st


def get_story(s: Session, id: int) -> Story | None:
    return s.get(Story, id)


def list_stories(s: Session, epic_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Story).filter(Story.epic_id == epic_id)
    return _paginate(q, limit, offset).all()


def update_story(s: Session, id: int, **fields) -> Story | None:
    st = s.get(Story, id)
    if not st:
        return None
    for k, v in fields.items():
        if k in ("title", "description", "status") and v is not None:
            if k == "title":
                v = _required(v, "title", 300)
            elif k == "status":
                _check_status(v)
            setattr(st, k, v)
    _commit(s); s.refresh(st); return st


def delete_story(s: Session, id: int) -> bool:
    st = s.get(Story, id)
    if not st:
        return False
    task_ids = [x[0] for x in s.query(Task.id).filter(Task.story_id == id).all()]
    if task_ids:
        s.query(Comment).filter(Comment.task_id.in_(task_ids)).delete(synchronize_session=False)
    s.query(Task).filter(Task.story_id == id).delete()
    s.delete(st); _commit(s); return True


# ---------- Task ----------
def create_task(s: Session, *, project_id: int, story_id: int | None, title: str,
                type: str = ItemType.TASK, description: str = "", spec: str = "",
                priority: str = Priority.MEDIUM, sprint_id: int | None = None,
                assignee_id: int | None = None, due_date=None, labels: str = "[]") -> Task:
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
             assignee_id=assignee_id, due_date=due_date, labels=labels or "[]")
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


def update_task(s: Session, id: int, **fields) -> Task | None:
    t = s.get(Task, id)
    if not t:
        return None
    allowed = {"title", "description", "spec", "type", "status", "priority", "sprint_id",
               "assignee_id", "due_date", "labels"}  # Epic 17
    for k, v in fields.items():
        if k in allowed and v is not None:
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


def set_status(s: Session, id: int, new_status: str) -> Task | None:
    t = s.get(Task, id)
    if not t:
        raise NotFound(f"task {id} not found")
    _check_status(new_status)
    new = Status(new_status)
    current = Status(t.status)
    if current != new and new not in TRANSITIONS.get(current, set()):
        raise IllegalTransition(f"{t.status} -> {new} 不合法")
    old_status = t.status
    t.status = new
    _commit(s); s.refresh(t)
    # 状态变更时清除项目统计缓存
    if old_status != new:
        _invalidate_project_stats_cache(t.project_id)
    return t


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
def create_comment(s: Session, *, task_id: int, author: str, content: str) -> Comment:
    if not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    author, content = (author or "").strip(), (content or "").strip()
    if not author or not content:
        raise InvalidValue("author and content are required")
    comment = Comment(task_id=task_id, author=author[:100], content=content)
    s.add(comment); _commit(s); s.refresh(comment); return comment


def list_comments(s: Session, task_id: int):
    if not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    return s.query(Comment).filter(Comment.task_id == task_id).order_by(Comment.created_at, Comment.id).all()


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


def create_schedule(s: Session, *, project_id: int, title: str,
                    schedule_type: str = "cron", cron_expr: str | None = None) -> AgentSchedule:
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
    sch = AgentSchedule(project_id=project_id, title=title,
                        schedule_type=schedule_type, cron_expr=cron_expr)
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
        elif k == "started_at" and v is not None:
            run.started_at = v
        elif k == "finished_at" and v is not None:
            run.finished_at = v
        elif k == "task_id" and v is not None:
            run.task_id = v
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
            s.query(func.count(ProjectMember.id))
            .filter(ProjectMember.project_id == p.id)
            .scalar()
        ) or 0
        result.append(row)
    return result, total


# ---------- Visibility-filtered project list ----------
def list_accessible_projects(
    s: Session, user_id: int | None, limit: int | None = None, offset: int = 0,
) -> tuple[list, int]:
    """返回用户可见的项目列表（public 项目 + 用户所在的 private 项目）"""
    if user_id is None:
        # 未登录：只能看 public 项目
        q = s.query(Project).filter(Project.is_private == False)
    else:
        # 查看用户是成员的 private 项目
        member_project_ids = [
            r[0]
            for r in s.query(ProjectMember.project_id)
            .filter(ProjectMember.user_id == user_id)
            .all()
        ]
        if member_project_ids:
            q = s.query(Project).filter(
                or_(
                    Project.is_private == False,
                    Project.id.in_(member_project_ids),
                )
            )
        else:
            q = s.query(Project).filter(Project.is_private == False)
    total = q.count()
    return _paginate(q.order_by(Project.id.desc()), limit, offset).all(), total


# ---------- Epic 20: 批量操作 ----------
def batch_update_task_status(s: Session, task_ids: list[int], new_status: str) -> dict:
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
        if current != new and new not in TRANSITIONS.get(current, set()):
            errors.append({"id": tid, "error": f"illegal transition {t.status} -> {new}"})
            continue
        t.status = new
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
