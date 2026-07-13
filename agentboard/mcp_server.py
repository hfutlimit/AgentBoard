"""AgentBoard MCP 服务（独立）。

所有业务数据均通过 REST API 访问，地址由 AGENTBOARD_API_URL 配置
（默认 http://127.0.0.1:8000）。MCP 服务不直接连接数据库。

运行：python -m agentboard.mcp_server   （stdio 传输）
"""
import os
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.dependencies import get_access_token

from . import auth as agent_auth

API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:58124")
# MCP 暂时保持开放；只有显式开启时才安装传输层 TokenVerifier。
MCP_REQUIRE_AUTH = os.getenv("AGENTBOARD_MCP_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes"}


class AgentBoardTokenVerifier(TokenVerifier):
    """让远程 MCP 与 AgentBoard REST/Web 共用同一枚登录 Token。"""

    async def verify_token(self, token: str) -> AccessToken | None:
        details = agent_auth.parse_token_details(token)
        if details is None:
            return None
        user_id, expires_at = details
        return AccessToken(
            token=token,
            client_id=f"agentboard-user-{user_id}",
            subject=str(user_id),
            scopes=["agentboard:read", "agentboard:write"],
            expires_at=expires_at,
            claims={"user_id": user_id},
        )


mcp = FastMCP("AgentBoard", auth=AgentBoardTokenVerifier() if MCP_REQUIRE_AUTH else None)


# ===================== HTTP API client =====================
import httpx

def _current_token():
    try:
        access = get_access_token()
    except RuntimeError:
        access = None
    return access.token if access else os.getenv("AGENTBOARD_MCP_TOKEN")

def _http(method, path, **kw):
    headers = dict(kw.pop("headers", {}) or {})
    token = _current_token()
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(base_url=API_URL, timeout=15) as c:
        r = c.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                return {"error": r.json().get("detail", r.text)}
            except Exception:
                return {"error": r.text}
        return r.json() if r.content else {"ok": True}

def _proj_list(limit=None, offset=0):
    resp = _http("GET", "/api/projects", params={"limit": limit, "offset": offset} if limit is not None else {})
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _proj_create(name, key, description):
    return _http("POST", "/api/projects", json={"name": name, "key": key, "description": description})

def _proj_get(project_id):
    return _http("GET", f"/api/projects/{project_id}")

def _proj_update(project_id, fields):
    return _http("PATCH", f"/api/projects/{project_id}", json=fields)

def _proj_delete(project_id):
    return _http("DELETE", f"/api/projects/{project_id}")

def _epic_list(project_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/projects/{project_id}/epics", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _epic_create(project_id, title, description):
    return _http("POST", f"/api/projects/{project_id}/epics", json={"title": title, "description": description})

def _story_create(epic_id, title, description):
    return _http("POST", f"/api/epics/{epic_id}/stories", json={"title": title, "description": description})

def _story_list(epic_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/epics/{epic_id}/stories", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _task_list(story_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/stories/{story_id}/tasks", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _task_create(project_id, story_id, title, type, description, spec, priority="medium"):
    return _http("POST", f"/api/stories/{story_id}/tasks",
                 json={"project_id": project_id, "title": title, "type": type,
                       "description": description, "spec": spec, "priority": priority})

def _task_get(task_id):
    return _http("GET", f"/api/tasks/{task_id}")

def _task_update(task_id, fields):
    return _http("PATCH", f"/api/tasks/{task_id}", json=fields)

def _task_append_spec(task_id, text):
    return _http("POST", f"/api/tasks/{task_id}/spec/append", json={"text": text})

def _task_delete(task_id):
    return _http("DELETE", f"/api/tasks/{task_id}")

def _task_status(task_id, status):
    return _http("PUT", f"/api/tasks/{task_id}/status", json={"status": status})

def _task_search(params):
    clean = {k: v for k, v in params.items() if v is not None}
    resp = _http("GET", "/api/tasks", params=clean)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _task_generated(task_id):
    return _http("POST", f"/api/tasks/{task_id}/generate-subtasks")

def _epic_get(epic_id):
    return _http("GET", f"/api/epics/{epic_id}")

def _epic_update(epic_id, fields):
    return _http("PATCH", f"/api/epics/{epic_id}", json=fields)

def _epic_delete(epic_id):
    return _http("DELETE", f"/api/epics/{epic_id}")

def _story_get(story_id):
    return _http("GET", f"/api/stories/{story_id}")

def _story_update(story_id, fields):
    return _http("PATCH", f"/api/stories/{story_id}", json=fields)

def _story_delete(story_id):
    return _http("DELETE", f"/api/stories/{story_id}")

def _comment_list(task_id):
    return _http("GET", f"/api/tasks/{task_id}/comments")

def _comment_create(task_id, author, content):
    return _http("POST", f"/api/tasks/{task_id}/comments",
                 json={"author": author, "content": content})

def _comment_delete(comment_id):
    return _http("DELETE", f"/api/comments/{comment_id}")

def _auth_register(username, password):
    return _http("POST", "/api/auth/register", json={"username": username, "password": password})

def _auth_login(username, password):
    return _http("POST", "/api/auth/login", json={"username": username, "password": password})

def _auth_me(token):
    return _http("GET", "/api/auth/me", headers={"Authorization": f"Bearer {token}"})

# ---------- Sprint ----------
def _sprint_list(project_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    resp = _http("GET", f"/api/projects/{project_id}/sprints", params=params)
    return resp.get("items", resp) if isinstance(resp, dict) else resp

def _sprint_get(sprint_id):
    return _http("GET", f"/api/sprints/{sprint_id}")

def _sprint_create(project_id, title, goal="", start_date=None, end_date=None):
    body = {"title": title, "goal": goal}
    if start_date:
        body["start_date"] = start_date
    if end_date:
        body["end_date"] = end_date
    return _http("POST", f"/api/projects/{project_id}/sprints", json=body)

def _sprint_update(sprint_id, fields):
    return _http("PATCH", f"/api/sprints/{sprint_id}", json=fields)

def _sprint_activate(sprint_id):
    return _http("POST", f"/api/sprints/{sprint_id}/activate")

def _sprint_complete(sprint_id):
    return _http("POST", f"/api/sprints/{sprint_id}/complete")

def _sprint_delete(sprint_id):
    return _http("DELETE", f"/api/sprints/{sprint_id}")

def _sprint_task_list(sprint_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    return _http("GET", f"/api/sprints/{sprint_id}/tasks", params=params)

# ---------- AgentSchedule ----------
def _schedule_list(project_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    return _http("GET", f"/api/projects/{project_id}/schedules", params=params)

def _schedule_get(schedule_id):
    return _http("GET", f"/api/schedules/{schedule_id}")

def _schedule_create(project_id, title, schedule_type="cron", cron_expr=None):
    body = {"title": title, "schedule_type": schedule_type}
    if cron_expr:
        body["cron_expr"] = cron_expr
    return _http("POST", f"/api/projects/{project_id}/schedules", json=body)

def _schedule_update(schedule_id, fields):
    return _http("PATCH", f"/api/schedules/{schedule_id}", json=fields)

def _schedule_delete(schedule_id):
    return _http("DELETE", f"/api/schedules/{schedule_id}")

# ---------- AgentRun ----------
def _run_create(schedule_id, task_id=None, idempotency_key=None):
    body = {}
    if task_id is not None:
        body["task_id"] = task_id
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return _http("POST", f"/api/schedules/{schedule_id}/runs", json=body)

def _run_list(schedule_id, limit=None, offset=0):
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    return _http("GET", f"/api/schedules/{schedule_id}/runs", params=params)

def _run_get(run_id):
    return _http("GET", f"/api/runs/{run_id}")

def _run_update(run_id, fields):
    return _http("PATCH", f"/api/runs/{run_id}", json=fields)

def _run_delete(run_id):
    return _http("DELETE", f"/api/runs/{run_id}")


# ===================== MCP 工具 =====================
@mcp.tool()
def list_projects(limit: int | None = None, offset: int = 0) -> list:
    """列出所有项目。limit / offset 用于分页。"""
    return _proj_list(limit=limit, offset=offset)


@mcp.tool()
def get_project(project_id: int) -> dict:
    """获取 Project 详情。"""
    return _proj_get(project_id)


@mcp.tool()
def create_project(name: str, key: str | None = None, description: str = "") -> dict:
    """创建项目。name 必填，key 为短码，description 为 markdown。"""
    return _proj_create(name, key, description)


@mcp.tool()
def update_project(project_id: int, name: str | None = None, key: str | None = None,
                   description: str | None = None) -> dict:
    """更新 Project 名称、短码或 markdown 描述。"""
    fields = {k: v for k, v in dict(name=name, key=key, description=description).items() if v is not None}
    return _proj_update(project_id, fields)


@mcp.tool()
def delete_project(project_id: int) -> dict:
    """删除 Project 及其全部 Epic、Story、Task 和评论。"""
    return _proj_delete(project_id)


@mcp.tool()
def list_epics(project_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Project 下的 Epic。"""
    return _epic_list(project_id, limit=limit, offset=offset)


@mcp.tool()
def create_epic(project_id: int, title: str, description: str = "") -> dict:
    """在指定项目下创建 Epic。"""
    return _epic_create(project_id, title, description)


@mcp.tool()
def create_story(epic_id: int, title: str, description: str = "") -> dict:
    """在指定 Epic 下创建 Story。"""
    return _story_create(epic_id, title, description)


@mcp.tool()
def list_stories(epic_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Epic 下的 Story。"""
    return _story_list(epic_id, limit=limit, offset=offset)


@mcp.tool()
def get_epic(epic_id: int) -> dict:
    """获取 Epic 详情。"""
    return _epic_get(epic_id)


@mcp.tool()
def update_epic(epic_id: int, title: str | None = None, description: str | None = None,
                status: str | None = None) -> dict:
    """更新 Epic 标题/描述/状态。"""
    fields = {k: v for k, v in dict(title=title, description=description, status=status).items() if v is not None}
    return _epic_update(epic_id, fields)


@mcp.tool()
def delete_epic(epic_id: int) -> dict:
    """删除 Epic（级联删除其 Stories / Tasks）。"""
    return _epic_delete(epic_id)


@mcp.tool()
def get_story(story_id: int) -> dict:
    """获取 Story 详情。"""
    return _story_get(story_id)


@mcp.tool()
def update_story(story_id: int, title: str | None = None, description: str | None = None,
                status: str | None = None) -> dict:
    """更新 Story 标题/描述/状态。"""
    fields = {k: v for k, v in dict(title=title, description=description, status=status).items() if v is not None}
    return _story_update(story_id, fields)


@mcp.tool()
def delete_story(story_id: int) -> dict:
    """删除 Story（级联删除其 Tasks）。"""
    return _story_delete(story_id)


@mcp.tool()
def list_tasks(story_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Story 下的 Task/Bug。"""
    return _task_list(story_id, limit=limit, offset=offset)


@mcp.tool()
def create_task(project_id: int, story_id: int, title: str,
                type: str = "task", description: str = "", spec: str = "",
                priority: str = "medium") -> dict:
    """在指定 Story 下创建 Task/Bug，可设置五级 priority。"""
    return _task_create(project_id, story_id, title, type, description, spec, priority)


@mcp.tool()
def get_task(task_id: int) -> dict:
    """获取任务详情（含 description 与 spec）。"""
    return _task_get(task_id)


@mcp.tool()
def update_task(task_id: int, title: str | None = None, description: str | None = None,
                spec: str | None = None, type: str | None = None,
                priority: str | None = None) -> dict:
    """更新任务标题/描述/spec/类型/优先级。"""
    fields = {k: v for k, v in dict(title=title, description=description, spec=spec,
                                    type=type, priority=priority).items() if v is not None}
    return _task_update(task_id, fields)


@mcp.tool()
def delete_task(task_id: int) -> dict:
    """删除 Task/Bug 及其评论。"""
    return _task_delete(task_id)


@mcp.tool()
def set_task_spec(task_id: int, spec: str) -> dict:
    """设置任务 spec（OpenSpec/Superpowers 风格 markdown）。"""
    return _task_update(task_id, {"spec": spec})


@mcp.tool()
def get_task_spec(task_id: int) -> dict:
    """读取任务 spec 原文。"""
    t = _task_get(task_id)
    if "error" in t:
        return t
    return {"task_id": task_id, "spec": t.get("spec", "")}


@mcp.tool()
def append_task_spec(task_id: int, text: str) -> dict:
    """在任务现有 spec 末尾追加 markdown 文本。"""
    return _task_append_spec(task_id, text)


@mcp.tool()
def set_status(task_id: int, status: str) -> dict:
    """变更任务状态（校验合法迁移，见文档 FR-5）。"""
    return _task_status(task_id, status)


@mcp.tool()
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, type: str | None = None,
                 status: str | None = None, priority: str | None = None,
                 q: str | None = None,
                 limit: int | None = None, offset: int = 0) -> list:
    """按条件搜索任务，可按 priority 筛选；q 匹配 title/description/spec。"""
    return _task_search(dict(project_id=project_id, epic_id=epic_id, story_id=story_id,
                             type=type, status=status, priority=priority, q=q,
                             limit=limit, offset=offset))


@mcp.tool()
def list_comments(task_id: int) -> list | dict:
    """按时间顺序读取任务评论，供人类与开发 Agent 共享进展。"""
    return _comment_list(task_id)


@mcp.tool()
def add_comment(task_id: int, author: str, content: str) -> dict:
    """给任务追加 markdown 评论；Agent 可用它同步开始、阻塞和完成状态。"""
    return _comment_create(task_id, author, content)


@mcp.tool()
def delete_comment(comment_id: int) -> dict:
    """删除指定评论。"""
    return _comment_delete(comment_id)


@mcp.tool()
def spec_proposal(task_id: int, title: str, background: str, goal: str,
                  scope: str, tasks: str, acceptance: str) -> dict:
    """生成 OpenSpec 风格变更提案并写入任务 spec。"""
    md = (
        f"# 变更提案：{title}\n\n"
        f"## 背景\n{background}\n\n"
        f"## 目标\n{goal}\n\n"
        f"## 范围\n{scope}\n\n"
        f"## 任务清单\n{tasks}\n\n"
        f"## 验收标准\n{acceptance}\n"
    )
    return _task_update(task_id, {"spec": md})


@mcp.tool()
def generate_tasks_from_spec(task_id: int) -> list:
    """从任务 spec 的清单项（- [ ] 标题）生成同级子任务，并在 spec 中回写链接。

    返回生成的子任务列表（含 id）；源任务通过 source_spec_id 反向关联。
    """
    return _task_generated(task_id)


# ---------- Sprint MCP 工具 ----------
@mcp.tool()
def list_sprints(project_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Project 下的 Sprint。"""
    return _sprint_list(project_id, limit=limit, offset=offset)


@mcp.tool()
def get_sprint(sprint_id: int) -> dict:
    """获取 Sprint 详情（含 goal、日期、状态）。"""
    return _sprint_get(sprint_id)


@mcp.tool()
def create_sprint(project_id: int, title: str, goal: str = "",
                  start_date: str | None = None, end_date: str | None = None) -> dict:
    """在指定项目下创建 Sprint。start_date / end_date 为 ISO 日期字符串 (YYYY-MM-DD)。"""
    return _sprint_create(project_id, title, goal=goal, start_date=start_date, end_date=end_date)


@mcp.tool()
def update_sprint(sprint_id: int, title: str | None = None, goal: str | None = None,
                  start_date: str | None = None, end_date: str | None = None) -> dict:
    """更新 Sprint 标题/目标/日期。仅传入需要修改的字段。"""
    fields = {k: v for k, v in dict(title=title, goal=goal,
                                    start_date=start_date, end_date=end_date).items() if v is not None}
    return _sprint_update(sprint_id, fields)


@mcp.tool()
def activate_sprint(sprint_id: int) -> dict:
    """激活 Sprint（自动停用同项目其他 active Sprint）。"""
    return _sprint_activate(sprint_id)


@mcp.tool()
def complete_sprint(sprint_id: int) -> dict:
    """完成 Sprint（未完成的任务退回 backlog）。"""
    return _sprint_complete(sprint_id)


@mcp.tool()
def delete_sprint(sprint_id: int) -> dict:
    """删除 Sprint（ACTIVE 状态不可删除；关联任务解除绑定）。"""
    return _sprint_delete(sprint_id)


@mcp.tool()
def list_sprint_tasks(sprint_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Sprint 下的 Task。"""
    return _sprint_task_list(sprint_id, limit=limit, offset=offset)


# ---------- AgentSchedule MCP 工具 ----------
@mcp.tool()
def list_schedules(project_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Project 下的 AgentSchedule。"""
    return _schedule_list(project_id, limit=limit, offset=offset)


@mcp.tool()
def get_schedule(schedule_id: int) -> dict:
    """获取 AgentSchedule 详情。"""
    return _schedule_get(schedule_id)


@mcp.tool()
def create_schedule(project_id: int, title: str, schedule_type: str = "cron",
                    cron_expr: str | None = None) -> dict:
    """创建定时计划。schedule_type: once/cron；cron_expr: 5 字段 cron 表达式。"""
    return _schedule_create(project_id, title, schedule_type=schedule_type, cron_expr=cron_expr)


@mcp.tool()
def update_schedule(schedule_id: int, title: str | None = None,
                    schedule_type: str | None = None, cron_expr: str | None = None,
                    enabled: bool | None = None, next_run_at: str | None = None) -> dict:
    """更新 AgentSchedule 配置。仅传入需要修改的字段。"""
    fields = {k: v for k, v in dict(title=title, schedule_type=schedule_type,
                                    cron_expr=cron_expr, enabled=enabled,
                                    next_run_at=next_run_at).items() if v is not None}
    return _schedule_update(schedule_id, fields)


@mcp.tool()
def delete_schedule(schedule_id: int) -> dict:
    """删除 AgentSchedule（级联删除运行记录）。"""
    return _schedule_delete(schedule_id)


# ---------- AgentRun MCP 工具 ----------
@mcp.tool()
def list_runs(schedule_id: int, limit: int | None = None, offset: int = 0) -> list:
    """分页列出指定 Schedule 的 AgentRun 历史。"""
    return _run_list(schedule_id, limit=limit, offset=offset)


@mcp.tool()
def get_run(run_id: int) -> dict:
    """获取 AgentRun 详情（含 output/error_message）。"""
    return _run_get(run_id)


@mcp.tool()
def create_run(schedule_id: int, task_id: int | None = None,
               idempotency_key: str | None = None) -> dict:
    """创建运行记录。idempotency_key 用于防止重复运行。"""
    return _run_create(schedule_id, task_id=task_id, idempotency_key=idempotency_key)


@mcp.tool()
def update_run(run_id: int, status: str | None = None, output: str | None = None,
               error_message: str | None = None, started_at: str | None = None,
               finished_at: str | None = None, task_id: int | None = None) -> dict:
    """更新 AgentRun 状态、输出或错误信息。"""
    fields = {k: v for k, v in dict(status=status, output=output, error_message=error_message,
                                    started_at=started_at, finished_at=finished_at,
                                    task_id=task_id).items() if v is not None}
    return _run_update(run_id, fields)


@mcp.tool()
def delete_run(run_id: int) -> dict:
    """删除运行记录。"""
    return _run_delete(run_id)


@mcp.tool()
def auth_register(username: str, password: str) -> dict:
    """注册 AgentBoard 用户并返回带有效期的登录 Token。"""
    return _auth_register(username, password)


@mcp.tool()
def auth_login(username: str, password: str) -> dict:
    """登录 AgentBoard 并返回带有效期的 Token。"""
    return _auth_login(username, password)


@mcp.tool()
def auth_me(token: str | None = None) -> dict:
    """校验显式 Token；未提供时使用当前远程 MCP Bearer Token。"""
    if token is None:
        try:
            access = get_access_token()
        except RuntimeError:
            access = None
        token = access.token if access else os.getenv("AGENTBOARD_MCP_TOKEN")
    if not token:
        return {"error": "unauthorized"}
    return _auth_me(token)


# ---------- Attachment MCP 工具 ----------
def _attachment_list(task_id):
    return _http("GET", f"/api/tasks/{task_id}/attachments")

def _attachment_get(attachment_id):
    return _http("GET", f"/api/attachments/{attachment_id}/info")


@mcp.tool()
def list_attachments(task_id: int) -> list | dict:
    """列出任务的所有附件元数据（不含文件内容）。"""
    return _attachment_list(task_id)


@mcp.tool()
def get_attachment_info(attachment_id: int) -> dict:
    """获取附件元数据（id、文件名、MIME、大小、上传时间）。"""
    return _attachment_get(attachment_id)


# ---------- Project Stats MCP 工具 ----------
def _project_stats(project_id):
    return _http("GET", f"/api/projects/{project_id}/stats")


@mcp.tool()
def get_project_stats(project_id: int) -> dict:
    """获取项目统计：总任务数、状态分布、每日新增/完成任务量、完成率。"""
    return _project_stats(project_id)


# ---------- Agent MCP 工具（Task 92）----------
def _agent_claim_task(task_id, agent_name="agent"):
    import uuid
    # 获取 task 详情
    t = _http("GET", f"/api/tasks/{task_id}")
    if "error" in t:
        return t
    # 创建 run（临时用 schedule_id=0 表示手动触发）
    idempotency_key = f"{agent_name}-{task_id}-{uuid.uuid4().hex[:8]}"
    run = _http("POST", "/api/schedules/0/runs" if False else f"/api/schedules/1/runs",
               json={"task_id": task_id, "idempotency_key": idempotency_key})
    # 同步任务状态
    if t.get("status") in ("backlog", "todo"):
        _http("PUT", f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
        t = _http("GET", f"/api/tasks/{task_id}")
    return {"run": run, "task": t, "schedule": None}

def _agent_heartbeat(run_id, status="running"):
    fields = {"status": status}
    return _http("PATCH", f"/api/runs/{run_id}", json=fields)

def _agent_complete_run(run_id, output, status="success", error_message=None):
    fields = {"status": status, "output": output}
    if error_message:
        fields["error_message"] = error_message
    return _http("PATCH", f"/api/runs/{run_id}", json=fields)


@mcp.tool()
def claim_task(task_id: int, agent_name: str = "agent") -> dict:
    """Agent 领取任务：
    - 创建 Run 记录
    - 自动将任务状态从 backlog/todo 推进到 in_progress
    - 返回 run 信息供后续 heartbeat/complete 使用
    """
    return _agent_claim_task(task_id, agent_name)


@mcp.tool()
def heartbeat(run_id: int, status: str = "running") -> dict:
    """Agent 心跳：定期调用以更新 Run 状态为 running。
    status 可选：pending / running / success / failed
    """
    return _agent_heartbeat(run_id, status)


@mcp.tool()
def complete_run(run_id: int, output: str, status: str = "success",
                error_message: str | None = None) -> dict:
    """Agent 完成运行：
    - output: 运行输出摘要（markdown）
    - status: success / failed
    - error_message: 失败原因（可选）
    - 成功时自动将关联任务推进到 in_review
    """
    return _agent_complete_run(run_id, output, status, error_message)


@mcp.tool()
def sync_status(task_id: int, status: str, comment: str | None = None) -> dict:
    """同步任务状态，可选追加评论。
    status 必须符合状态机合法迁移规则。
    """
    result = _task_status(task_id, status)
    if "error" in result:
        return result
    if comment:
        # 获取 task 详情以确定 project 用于评论 author
        t = _task_get(task_id)
        author = t.get("spec", "").split("\n")[0][:50] if t else "agent"
        _comment_create(task_id, author=author, content=comment)
    return result


if __name__ == "__main__":
    transport = os.getenv("AGENTBOARD_MCP_TRANSPORT", "stdio").lower()
    if transport in {"http", "streamable-http"}:
        secret = os.getenv("AGENTBOARD_SECRET", "dev-insecure-secret-change-me")
        if secret == "dev-insecure-secret-change-me" or len(secret) < 32:
            raise RuntimeError("remote MCP requires AGENTBOARD_SECRET with at least 32 characters")
        mcp.run(
            transport="http",
            host=os.getenv("AGENTBOARD_MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("AGENTBOARD_MCP_PORT", "8001")),
            path=os.getenv("AGENTBOARD_MCP_PATH", "/mcp"),
        )
    elif transport == "stdio":
        mcp.run()
    else:
        raise RuntimeError(f"unsupported AGENTBOARD_MCP_TRANSPORT: {transport}")
