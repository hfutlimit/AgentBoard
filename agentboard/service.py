import re
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import models, auth
from .models import (
    ItemType, Status, Priority, SprintStatus, ALL_TYPES, ALL_STATUSES,
    ALL_PRIORITIES, ALL_SPRINT_STATUSES,
    Project, Epic, Story, Task, Comment, Sprint, Attachment,
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
    "name", "key", "description",          # project
    "title", "description", "status",      # epic / story / task
    "type", "spec", "priority", "sprint_id",  # task
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
        if k in ("name", "key", "description") and v is not None:
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
                priority: str = Priority.MEDIUM, sprint_id: int | None = None) -> Task:
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
    t = Task(project_id=project_id, story_id=story_id, sprint_id=sprint_id,
             title=_required(title, "title", 300),
             type=type, description=description or "", spec=spec or "", priority=priority)
    s.add(t); _commit(s); s.refresh(t); return t


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
    allowed = {"title", "description", "spec", "type", "status", "priority", "sprint_id"}
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
            setattr(t, k, v)
    _commit(s); s.refresh(t); return t


def delete_task(s: Session, id: int) -> bool:
    t = s.get(Task, id)
    if not t:
        return False
    s.query(Comment).filter(Comment.task_id == id).delete(synchronize_session=False)
    s.delete(t); _commit(s); return True


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
    t.status = new
    _commit(s); s.refresh(t); return t


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
    u = models.User(username=username, password_hash=auth.hash_password(password))
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
