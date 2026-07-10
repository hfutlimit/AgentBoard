import re
from sqlalchemy import or_
from sqlalchemy.orm import Session
from . import models
from .models import ItemType, Status, Project, Epic, Story, Task

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
    "type", "spec",                        # task
}


def _ser(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[c.name] = v
    return out


# ---------- Project ----------
def create_project(s: Session, *, name: str, key=None, description: str = "") -> Project:
    p = Project(name=name, key=key or None, description=description or "")
    s.add(p); s.commit(); s.refresh(p); return p


def get_project(s: Session, id: int) -> Project | None:
    return s.get(Project, id)


def list_projects(s: Session, limit: int | None = None, offset: int = 0):
    q = s.query(Project).order_by(Project.id.desc())
    if limit is not None:
        q = q.limit(limit).offset(offset)
    return q.all()


def update_project(s: Session, id: int, **fields) -> Project | None:
    p = s.get(Project, id)
    if not p:
        return None
    for k, v in fields.items():
        if k in ("name", "key", "description") and v is not None:
            setattr(p, k, v)
    s.commit(); s.refresh(p); return p


def delete_project(s: Session, id: int) -> bool:
    p = s.get(Project, id)
    if not p:
        return False
    s.query(Task).filter(Task.project_id == id).delete()
    for ep in s.query(Epic).filter(Epic.project_id == id):
        s.query(Story).filter(Story.epic_id == ep.id).delete()
    s.query(Epic).filter(Epic.project_id == id).delete()
    s.delete(p); s.commit(); return True


# ---------- Epic ----------
def create_epic(s: Session, *, project_id: int, title: str, description: str = "") -> Epic:
    ep = Epic(project_id=project_id, title=title, description=description or "")
    s.add(ep); s.commit(); s.refresh(ep); return ep


def get_epic(s: Session, id: int) -> Epic | None:
    return s.get(Epic, id)


def list_epics(s: Session, project_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Epic).filter(Epic.project_id == project_id)
    if limit is not None:
        q = q.limit(limit).offset(offset)
    return q.all()


def update_epic(s: Session, id: int, **fields) -> Epic | None:
    ep = s.get(Epic, id)
    if not ep:
        return None
    for k, v in fields.items():
        if k in ("title", "description", "status") and v is not None:
            setattr(ep, k, v)
    s.commit(); s.refresh(ep); return ep


def delete_epic(s: Session, id: int) -> bool:
    ep = s.get(Epic, id)
    if not ep:
        return False
    for st in s.query(Story).filter(Story.epic_id == id):
        s.query(Task).filter(Task.story_id == st.id).delete()
    s.query(Story).filter(Story.epic_id == id).delete()
    s.delete(ep); s.commit(); return True


# ---------- Story ----------
def create_story(s: Session, *, epic_id: int, title: str, description: str = "") -> Story:
    st = Story(epic_id=epic_id, title=title, description=description or "")
    s.add(st); s.commit(); s.refresh(st); return st


def get_story(s: Session, id: int) -> Story | None:
    return s.get(Story, id)


def list_stories(s: Session, epic_id: int, limit: int | None = None, offset: int = 0):
    q = s.query(Story).filter(Story.epic_id == epic_id)
    if limit is not None:
        q = q.limit(limit).offset(offset)
    return q.all()


def update_story(s: Session, id: int, **fields) -> Story | None:
    st = s.get(Story, id)
    if not st:
        return None
    for k, v in fields.items():
        if k in ("title", "description", "status") and v is not None:
            setattr(st, k, v)
    s.commit(); s.refresh(st); return st


def delete_story(s: Session, id: int) -> bool:
    st = s.get(Story, id)
    if not st:
        return False
    s.query(Task).filter(Task.story_id == id).delete()
    s.delete(st); s.commit(); return True


# ---------- Task ----------
def create_task(s: Session, *, project_id: int, story_id: int | None, title: str,
                type: str = ItemType.TASK, description: str = "", spec: str = "") -> Task:
    t = Task(project_id=project_id, story_id=story_id, title=title,
             type=type, description=description or "", spec=spec or "")
    s.add(t); s.commit(); s.refresh(t); return t


def get_task(s: Session, id: int) -> Task | None:
    return s.get(Task, id)


def list_tasks(s: Session, story_id: int | None = None, limit: int | None = None, offset: int = 0):
    q = s.query(Task)
    if story_id is not None:
        q = q.filter(Task.story_id == story_id)
    q = q.order_by(Task.id.desc())
    if limit is not None:
        q = q.limit(limit).offset(offset)
    return q.all()


def update_task(s: Session, id: int, **fields) -> Task | None:
    t = s.get(Task, id)
    if not t:
        return None
    allowed = {"title", "description", "spec", "type", "status"}
    for k, v in fields.items():
        if k in allowed and v is not None:
            setattr(t, k, v)
    s.commit(); s.refresh(t); return t


def set_task_description(s: Session, id: int, text: str) -> Task | None:
    return update_task(s, id, description=text)


def set_task_spec(s: Session, id: int, text: str) -> Task | None:
    return update_task(s, id, spec=text)


def append_task_spec(s: Session, id: int, text: str) -> Task | None:
    t = s.get(Task, id)
    if not t:
        return None
    t.spec = (t.spec or "") + "\n" + text
    s.commit(); s.refresh(t); return t


def set_status(s: Session, id: int, new_status: str) -> Task | None:
    t = s.get(Task, id)
    if not t:
        raise NotFound(f"task {id} not found")
    new = Status(new_status)
    if t.status != new and new not in TRANSITIONS.get(Status(t.status), set()):
        raise IllegalTransition(f"{t.status} -> {new} 不合法")
    t.status = new
    s.commit(); s.refresh(t); return t


# ---------- Spec -> 子任务（OpenSpec / Superpowers 风格） ----------
def generate_tasks_from_spec(s: Session, task_id: int) -> list:
    """解析任务 spec 中的清单项（- [ ] 标题），生成同级子任务。

    生成的子任务：同 project / story，type=task，status=backlog，
    并通过 source_spec_id 反向关联到源任务；同时在源 spec 末尾回写链接。
    """
    src = s.get(Task, task_id)
    if not src:
        raise NotFound(f"task {task_id} not found")
    created = []
    for line in (src.spec or "").splitlines():
        m = re.match(r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)", line)
        if not m:
            continue
        title = m.group(1).strip()
        if not title:
            continue
        t = Task(project_id=src.project_id, story_id=src.story_id,
                 type=ItemType.TASK, title=title[:300], description=title,
                 source_spec_id=task_id)
        s.add(t)
        created.append(t)
    s.commit()
    for t in created:
        s.refresh(t)
    if created:
        links = "\n".join(f"- 子任务 #{t.id}: {t.title}" for t in created)
        src.spec = (src.spec or "") + f"\n\n## 生成的自任务\n{links}\n"
        s.commit(); s.refresh(src)
    return created


# ---------- Search ----------
def search_tasks(s: Session, *, project_id=None, epic_id=None, story_id=None,
                 type=None, status=None, q=None, limit: int | None = None, offset: int = 0):
    qry = s.query(Task)
    if project_id is not None:
        qry = qry.filter(Task.project_id == project_id)
    if story_id is not None:
        qry = qry.filter(Task.story_id == story_id)
    if type is not None:
        qry = qry.filter(Task.type == type)
    if status is not None:
        qry = qry.filter(Task.status == status)
    if epic_id is not None:
        qry = qry.join(Story, Task.story_id == Story.id).filter(Story.epic_id == epic_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Task.title.ilike(like), Task.description.ilike(like),
                              Task.spec.ilike(like)))
    qry = qry.order_by(Task.id.desc())
    if limit is not None:
        qry = qry.limit(limit).offset(offset)
    return qry.all()


class NotFound(Exception):
    pass


class IllegalTransition(Exception):
    pass
