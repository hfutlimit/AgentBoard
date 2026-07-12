"""AgentBoard REST API（纯 JSON，前后端分离的后端）。

独立运行：uvicorn agentboard.api:app --port 8000
供 Web 前端（fetch）与 MCP（httpx）调用；不含任何 HTML 渲染。
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, Header, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .database import get_session, init_db, SessionLocal
from . import service, auth
from .models import ALL_TYPES, ALL_STATUSES, ALL_PRIORITIES, ALL_SPRINT_STATUSES, ALL_SCHEDULE_TYPES, ALL_RUN_STATUSES


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.validate_runtime_security()
    init_db()
    yield


app = FastAPI(title="AgentBoard API", version="0.2", lifespan=lifespan)

# 前后端分离：允许 Web 前端跨域调用
_cors_origins = [
    x.strip() for x in os.getenv("AGENTBOARD_CORS_ORIGINS", "*").split(",") if x.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_business_auth(request: Request, call_next):
    """可选统一保护 REST 业务端点，避免新增路由遗漏鉴权依赖。"""
    protected = (
        os.getenv("AGENTBOARD_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes"}
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path not in {"/api/meta", "/api/auth/register", "/api/auth/login"}
    )
    if protected:
        authorization = request.headers.get("Authorization")
        token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
        uid = auth.parse_token(token)
        with SessionLocal() as s:
            if not uid or service.get_user(s, uid) is None:
                resp = JSONResponse(status_code=401, content={"detail": "unauthorized"})
                origin = request.headers.get("origin")
                if origin:
                    resp.headers["Access-Control-Allow-Origin"] = origin
                    resp.headers["Access-Control-Allow-Credentials"] = "true"
                return resp
    return await call_next(request)


# ---------- Schemas ----------
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    key: str | None = Field(None, max_length=20)
    description: str | None = None


class EpicIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class EpicPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None


class StoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class StoryPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None


class TaskIn(BaseModel):
    project_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    type: str = "task"
    description: str = ""
    spec: str = ""
    priority: str = "medium"


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    type: str | None = None
    description: str | None = None
    spec: str | None = None
    priority: str | None = None
    sprint_id: int | None = None


class CommentIn(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class StatusIn(BaseModel):
    status: str


class SpecAppendIn(BaseModel):
    text: str = Field(min_length=1)


class AuthRegister(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("username is required")
        return value


class AuthLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SprintIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    goal: str = ""
    start_date: str | None = None
    end_date: str | None = None


class SprintPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    goal: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ScheduleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    schedule_type: str = "cron"
    cron_expr: str | None = None


class SchedulePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    schedule_type: str | None = None
    cron_expr: str | None = None
    enabled: bool | None = None
    next_run_at: str | None = None


class RunIn(BaseModel):
    task_id: int | None = None
    idempotency_key: str | None = Field(None, max_length=128)


class RunPatch(BaseModel):
    status: str | None = None
    output: str | None = None
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    task_id: int | None = None


def _need(obj, what: str):
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return obj


def _current_user(authorization: str | None, s: Session):
    token = authorization.split(" ", 1)[1] if authorization and authorization.startswith("Bearer ") else None
    uid = auth.parse_token(token)
    u = service.get_user(s, uid) if uid else None
    if u is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return u


@app.exception_handler(service.NotFound)
async def handle_not_found(_request: Request, exc: service.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(service.Duplicate)
async def handle_duplicate(_request: Request, exc: service.Duplicate):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(service.InvalidValue)
async def handle_invalid_value(_request: Request, exc: service.InvalidValue):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(service.IllegalTransition)
async def handle_illegal_transition(_request: Request, exc: service.IllegalTransition):
    return JSONResponse(status_code=400, content={"detail": str(exc)})



# ---------- Meta ----------
@app.get("/api/meta")
def meta():
    return {"types": ALL_TYPES, "statuses": ALL_STATUSES, "priorities": ALL_PRIORITIES,
            "sprint_statuses": ALL_SPRINT_STATUSES,
            "schedule_types": ALL_SCHEDULE_TYPES, "run_statuses": ALL_RUN_STATUSES}


# ---------- Auth ----------
@app.post("/api/auth/register", status_code=201)
def register(body: AuthRegister, s: Session = Depends(get_session)):
    registration_open = os.getenv("AGENTBOARD_ALLOW_REGISTRATION", "1").lower() in {"1", "true", "yes"}
    if not registration_open and service.has_users(s):
        raise HTTPException(status_code=403, detail="registration is disabled")
    try:
        u = service.register_user(s, username=body.username, password=body.password)
    except service.Duplicate:
        raise HTTPException(status_code=409, detail=f"username '{body.username}' already exists")
    return {"id": u.id, "username": u.username, "token": auth.make_token(u.id)}


@app.post("/api/auth/login")
def login(body: AuthLogin, s: Session = Depends(get_session)):
    u = service.authenticate_user(s, username=body.username, password=body.password)
    if u is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"id": u.id, "username": u.username, "token": auth.make_token(u.id)}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    u = _current_user(authorization, s)
    return {"id": u.id, "username": u.username}


# ---------- Projects ----------
@app.get("/api/projects")
def list_projects(s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
                  offset: int = Query(0, ge=0)):
    return [service._ser(p) for p in service.list_projects(s, limit=limit, offset=offset)]


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
def list_epics(pid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0)):
    return [service._ser(e) for e in service.list_epics(s, pid, limit=limit, offset=offset)]


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
def list_stories(eid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
                 offset: int = Query(0, ge=0)):
    return [service._ser(x) for x in service.list_stories(s, eid, limit=limit, offset=offset)]


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
def list_tasks(sid: int, s: Session = Depends(get_session), limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0), sprint_id: int | None = Query(None)):
    return [service._ser(t) for t in service.list_tasks(s, sid, sprint_id=sprint_id, limit=limit, offset=offset)]


@app.post("/api/stories/{sid}/tasks", status_code=201)
def create_task(sid: int, body: TaskIn, s: Session = Depends(get_session)):
    story = _need(service.get_story(s, sid), "story")
    try:
        t = service.create_task(s, project_id=body.project_id, story_id=story.id,
                                title=body.title, type=body.type,
                                description=body.description, spec=body.spec,
                                priority=body.priority)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(t)


@app.get("/api/tasks/{tid}")
def get_task(tid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_task(s, tid), "task"))


@app.patch("/api/tasks/{tid}")
def update_task(tid: int, body: TaskPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_task(s, tid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
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
    if not service.delete_task(s, tid):
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}


@app.post("/api/tasks/{tid}/spec/append")
def append_task_spec(tid: int, body: SpecAppendIn, s: Session = Depends(get_session)):
    return service._ser(_need(service.append_task_spec(s, tid, body.text), "task"))


# ---------- Comments ----------
@app.get("/api/tasks/{tid}/comments")
def list_comments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(x) for x in service.list_comments(s, tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/tasks/{tid}/comments", status_code=201)
def create_comment(tid: int, body: CommentIn, s: Session = Depends(get_session)):
    try:
        return service._ser(service.create_comment(s, task_id=tid, author=body.author,
                                                   content=body.content))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/comments/{cid}")
def delete_comment(cid: int, s: Session = Depends(get_session)):
    if not service.delete_comment(s, cid):
        raise HTTPException(status_code=404, detail="comment not found")
    return {"ok": True}


@app.post("/api/tasks/{tid}/generate-subtasks")
def generate_subtasks(tid: int, s: Session = Depends(get_session)):
    try:
        created = service.generate_tasks_from_spec(s, tid)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return [service._ser(t) for t in created]


# ---------- Search ----------
@app.get("/api/tasks")
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, sprint_id: int | None = None,
                 type: str | None = None, status: str | None = None,
                 priority: str | None = None, q: str | None = Query(None),
                 limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                 s: Session = Depends(get_session)):
    try:
        rows = service.search_tasks(s, project_id=project_id, epic_id=epic_id,
                                    story_id=story_id, sprint_id=sprint_id,
                                    type=type, status=status,
                                    priority=priority, q=q, limit=limit, offset=offset)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(t) for t in rows]


# ---------- Sprint ----------
@app.get("/api/projects/{pid}/sprints")
def list_sprints(pid: int, s: Session = Depends(get_session),
                limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_project(s, pid), "project")
    return [service._ser(sp) for sp in service.list_sprints(s, pid, limit=limit, offset=offset)]


@app.post("/api/projects/{pid}/sprints", status_code=201)
def create_sprint(pid: int, body: SprintIn, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    return service._ser(service.create_sprint(
        s, project_id=pid, title=body.title, goal=body.goal,
        start_date=body.start_date, end_date=body.end_date))


@app.get("/api/sprints/{sid}")
def get_sprint(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_sprint(s, sid), "sprint"))


@app.patch("/api/sprints/{sid}")
def update_sprint(sid: int, body: SprintPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_sprint(s, sid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "sprint"))


@app.post("/api/sprints/{sid}/activate", status_code=200)
def activate_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        return service._ser(service.activate_sprint(s, sid))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/sprints/{sid}/complete", status_code=200)
def complete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        return service._ser(service.complete_sprint(s, sid))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/sprints/{sid}")
def delete_sprint(sid: int, s: Session = Depends(get_session)):
    try:
        if not service.delete_sprint(s, sid):
            raise HTTPException(status_code=404, detail="sprint not found")
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.get("/api/sprints/{sid}/tasks")
def list_sprint_tasks(sid: int, s: Session = Depends(get_session),
                      limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_sprint(s, sid), "sprint")
    return [service._ser(t) for t in service.list_tasks(s, sprint_id=sid, limit=limit, offset=offset)]


# ---------- Attachment ----------
@app.get("/api/tasks/{tid}/attachments")
def list_attachments(tid: int, s: Session = Depends(get_session)):
    try:
        return [service._ser(a) for a in service.list_attachments(s, tid)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/tasks/{tid}/attachments", status_code=201)
async def upload_attachment(tid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="failed to read file")
    try:
        att = service.create_attachment(s, task_id=tid, content=content,
                                         original_name=file.filename or "unnamed",
                                         mime_type=file.content_type or "application/octet-stream")
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(att)


@app.get("/api/attachments/{aid}")
def download_attachment(aid: int, s: Session = Depends(get_session)):
    att = service.get_attachment(s, aid)
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    path = service.get_attachment_path(att)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found on disk")
    return FileResponse(path, media_type=att.mime_type, filename=att.original_name)


@app.get("/api/attachments/{aid}/info")
def attachment_info(aid: int, s: Session = Depends(get_session)):
    att = service.get_attachment(s, aid)
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    return service._ser(att)


@app.delete("/api/attachments/{aid}")
def delete_attachment(aid: int, s: Session = Depends(get_session)):
    if not service.delete_attachment(s, aid):
        raise HTTPException(status_code=404, detail="attachment not found")
    return {"ok": True}


# ---------- AgentSchedule ----------
@app.get("/api/projects/{pid}/schedules")
def list_schedules(pid: int, s: Session = Depends(get_session),
                   limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_project(s, pid), "project")
    return [service._ser(sch) for sch in service.list_schedules(s, pid, limit=limit, offset=offset)]


@app.post("/api/projects/{pid}/schedules", status_code=201)
def create_schedule(pid: int, body: ScheduleIn, s: Session = Depends(get_session)):
    _need(service.get_project(s, pid), "project")
    try:
        sch = service.create_schedule(s, project_id=pid, title=body.title,
                                      schedule_type=body.schedule_type,
                                      cron_expr=body.cron_expr)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(sch)


@app.get("/api/schedules/{sid}")
def get_schedule(sid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_schedule(s, sid), "schedule"))


@app.patch("/api/schedules/{sid}")
def update_schedule(sid: int, body: SchedulePatch, s: Session = Depends(get_session)):
    try:
        r = service.update_schedule(s, sid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "schedule"))


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: int, s: Session = Depends(get_session)):
    if not service.delete_schedule(s, sid):
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True}


# ---------- AgentRun ----------
@app.post("/api/schedules/{sid}/runs", status_code=201)
def create_run(sid: int, body: RunIn, s: Session = Depends(get_session)):
    try:
        run = service.create_run(s, schedule_id=sid, task_id=body.task_id,
                                 idempotency_key=body.idempotency_key)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.Duplicate as e:
        raise HTTPException(status_code=409, detail=str(e))
    return service._ser(run)


@app.get("/api/schedules/{sid}/runs")
def list_runs(sid: int, s: Session = Depends(get_session),
              limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    _need(service.get_schedule(s, sid), "schedule")
    return [service._ser(r) for r in service.list_runs(s, sid, limit=limit, offset=offset)]


@app.get("/api/runs/{rid}")
def get_run(rid: int, s: Session = Depends(get_session)):
    return service._ser(_need(service.get_run(s, rid), "run"))


@app.patch("/api/runs/{rid}")
def update_run(rid: int, body: RunPatch, s: Session = Depends(get_session)):
    try:
        r = service.update_run(s, rid, **body.model_dump(exclude_none=True))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(_need(r, "run"))


@app.delete("/api/runs/{rid}")
def delete_run(rid: int, s: Session = Depends(get_session)):
    if not service.delete_run(s, rid):
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}
