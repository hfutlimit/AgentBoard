"""AgentBoard REST API（纯 JSON，前后端分离的后端）。

独立运行：uvicorn agentboard.api:app --port 8000
供 Web 前端（fetch）与 MCP（httpx）调用；不含任何 HTML 渲染。
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import get_session, init_db
from . import service
from .models import ALL_TYPES, ALL_STATUSES


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AgentBoard API", version="0.2", lifespan=lifespan)

# 前后端分离：允许 Web 前端跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Schemas ----------
class ProjectIn(BaseModel):
    name: str
    key: str | None = None
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    key: str | None = None
    description: str | None = None


class EpicIn(BaseModel):
    title: str
    description: str = ""


class EpicPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class StoryIn(BaseModel):
    title: str
    description: str = ""


class StoryPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class TaskIn(BaseModel):
    project_id: int
    title: str
    type: str = "task"
    description: str = ""
    spec: str = ""


class TaskPatch(BaseModel):
    title: str | None = None
    type: str | None = None
    description: str | None = None
    spec: str | None = None


class StatusIn(BaseModel):
    status: str


def _need(obj, what: str):
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return obj


# ---------- Meta ----------
@app.get("/api/meta")
def meta():
    return {"types": ALL_TYPES, "statuses": ALL_STATUSES}


# ---------- Projects ----------
@app.get("/api/projects")
def list_projects(s: Session = Depends(get_session)):
    return [service._ser(p) for p in service.list_projects(s)]


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn, s: Session = Depends(get_session)):
    return service._ser(service.create_project(s, name=body.name, key=body.key, description=body.description))


@app.get("/api/projects/{pid}")
def get_project(pid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_project(s, pid), "project"))


@app.patch("/api/projects/{pid}")
def update_project(pid: int, body: ProjectPatch, s: Session = Depends(get_session)):
    r = service.update_project(s, pid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "project"))


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, s: Session = Depends(get_session)):
    if not service.delete_project(s, pid):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


# ---------- Epics ----------
@app.get("/api/projects/{pid}/epics")
def list_epics(pid: int, s: Session = Depends(get_session)):
    return [service._ser(e) for e in service.list_epics(s, pid)]


@app.post("/api/projects/{pid}/epics", status_code=201)
def create_epic(pid: int, body: EpicIn, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    return service._ser(service.create_epic(s, project_id=pid, title=body.title, description=body.description))


@app.get("/api/epics/{eid}")
def get_epic(eid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_epic(s, eid), "epic"))


@app.patch("/api/epics/{eid}")
def update_epic(eid: int, body: EpicPatch, s: Session = Depends(get_session)):
    r = service.update_epic(s, eid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "epic"))


@app.delete("/api/epics/{eid}")
def delete_epic(eid: int, s: Session = Depends(get_session)):
    if not service.delete_epic(s, eid):
        raise HTTPException(status_code=404, detail="epic not found")
    return {"ok": True}


# ---------- Stories ----------
@app.get("/api/epics/{eid}/stories")
def list_stories(eid: int, s: Session = Depends(get_session)):
    return [service._ser(x) for x in service.list_stories(s, eid)]


@app.post("/api/epics/{eid}/stories", status_code=201)
def create_story(eid: int, body: StoryIn, s: Session = Depends(get_session)):
    _need(service.get_epic(s, eid), "epic")
    return service._ser(service.create_story(s, epic_id=eid, title=body.title, description=body.description))


@app.get("/api/stories/{sid}")
def get_story(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_story(s, sid), "story"))


@app.patch("/api/stories/{sid}")
def update_story(sid: int, body: StoryPatch, s: Session = Depends(get_session)):
    r = service.update_story(s, sid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "story"))


@app.delete("/api/stories/{sid}")
def delete_story(sid: int, s: Session = Depends(get_session)):
    if not service.delete_story(s, sid):
        raise HTTPException(status_code=404, detail="story not found")
    return {"ok": True}


# ---------- Tasks ----------
@app.get("/api/stories/{sid}/tasks")
def list_tasks(sid: int, s: Session = Depends(get_session)):
    return [service._ser(t) for t in service.list_tasks(s, sid)]


@app.post("/api/stories/{sid}/tasks", status_code=201)
def create_task(sid: int, body: TaskIn, s: Session = Depends(get_session)):
    story = _need(service.get_story(s, sid), "story")
    t = service.create_task(s, project_id=body.project_id, story_id=story.id,
                            title=body.title, type=body.type,
                            description=body.description, spec=body.spec)
    return service._ser(t)


@app.get("/api/tasks/{tid}")
def get_task(tid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_task(s, tid), "task"))


@app.patch("/api/tasks/{tid}")
def update_task(tid: int, body: TaskPatch, s: Session = Depends(get_session)):
    r = service.update_task(s, tid, **body.model_dump(exclude_none=True))
    return service._ser(_need(r, "task"))


@app.put("/api/tasks/{tid}/status")
def set_status(tid: int, body: StatusIn, s: Session = Depends(get_session)):
    try:
        return service._ser(service.set_status(s, tid, body.status))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, s: Session = Depends(get_session)):
    t = service.get_task(s, tid)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    s.delete(t); s.commit()
    return {"ok": True}


# ---------- Search ----------
@app.get("/api/tasks")
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, type: str | None = None,
                 status: str | None = None, q: str | None = Query(None),
                 s: Session = Depends(get_session)):
    rows = service.search_tasks(s, project_id=project_id, epic_id=epic_id,
                                story_id=story_id, type=type, status=status, q=q)
    return [service._ser(t) for t in rows]
