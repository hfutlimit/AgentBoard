"""AgentBoard Web API（FastAPI）+ 服务端渲染页面。与 MCP 共用同一 service 层。"""
import markdown as md
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_session, init_db
from . import service
from .models import ItemType, Status

TEMPLATES = str(Path(__file__).parent / "web" / "templates")
templates = Jinja2Templates(directory=TEMPLATES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AgentBoard", lifespan=lifespan)


def _render(text: str) -> str:
    return md.markdown(text or "", extensions=["fenced_code", "tables"])


def _404(detail: str):
    raise HTTPException(status_code=404, detail=detail)


# ---------- Pages ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, s: Session = Depends(get_session)):
    projects = service.list_projects(s)
    return templates.TemplateResponse(request, "index.html", {"projects": projects})


@app.get("/projects/{pid}", response_class=HTMLResponse)
def project_detail(pid: int, request: Request, s: Session = Depends(get_session)):
    p = service.get_project(s, pid) or _404("project not found")
    epics = service.list_epics(s, pid)
    return templates.TemplateResponse(request, "project.html", {"project": p, "epics": epics})


@app.get("/epics/{eid}", response_class=HTMLResponse)
def epic_detail(eid: int, request: Request, s: Session = Depends(get_session)):
    ep = s.get(service.Epic, eid) or _404("epic not found")
    stories = service.list_stories(s, eid)
    return templates.TemplateResponse(request, "epic.html", {"epic": ep, "stories": stories})


@app.get("/stories/{sid}", response_class=HTMLResponse)
def story_detail(sid: int, request: Request, s: Session = Depends(get_session)):
    st = s.get(service.Story, sid) or _404("story not found")
    tasks = service.list_tasks(s, sid)
    types = [ItemType.TASK, ItemType.BUG]
    return templates.TemplateResponse(request, "story.html", {"story": st, "tasks": tasks, "types": types})


@app.get("/tasks/{tid}", response_class=HTMLResponse)
def task_detail(tid: int, request: Request, s: Session = Depends(get_session)):
    t = service.get_task(s, tid) or _404("task not found")
    statuses = [x.value for x in Status]
    return templates.TemplateResponse(request, "task.html", {
        "task": t, "statuses": statuses,
        "desc_html": _render(t.description), "spec_html": _render(t.spec),
    })


# ---------- Create forms ----------
@app.post("/projects")
def create_project_post(name: str = Form(...), key: str = Form(""), description: str = Form(""),
                        s: Session = Depends(get_session)):
    service.create_project(s, name=name, key=key or None, description=description)
    return RedirectResponse("/", status_code=303)


@app.post("/projects/{pid}/epics")
def create_epic_post(pid: int, title: str = Form(...), description: str = Form(""),
                     s: Session = Depends(get_session)):
    service.create_epic(s, project_id=pid, title=title, description=description)
    return RedirectResponse(f"/projects/{pid}", status_code=303)


@app.post("/epics/{eid}/stories")
def create_story_post(eid: int, title: str = Form(...), description: str = Form(""),
                      s: Session = Depends(get_session)):
    st = service.create_story(s, epic_id=eid, title=title, description=description)
    return RedirectResponse(f"/epics/{eid}", status_code=303)


@app.post("/stories/{sid}/tasks")
def create_task_post(sid: int, project_id: int = Form(...), title: str = Form(...),
                     type: str = Form("task"), description: str = Form(""),
                     s: Session = Depends(get_session)):
    service.create_task(s, project_id=project_id, story_id=sid, title=title, type=type, description=description)
    return RedirectResponse(f"/stories/{sid}", status_code=303)


# ---------- Edit task ----------
@app.post("/tasks/{tid}")
def task_update(tid: int, description: str = Form(""), spec: str = Form(""),
                s: Session = Depends(get_session)):
    service.update_task(s, tid, description=description, spec=spec)
    return RedirectResponse(f"/tasks/{tid}", status_code=303)


@app.post("/tasks/{tid}/status")
def task_status(tid: int, status: str = Form(...), s: Session = Depends(get_session)):
    try:
        service.set_status(s, tid, status)
    except (service.NotFound, service.IllegalTransition) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/tasks/{tid}", status_code=303)
