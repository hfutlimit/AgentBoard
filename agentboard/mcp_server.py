"""AgentBoard MCP 服务（独立）。

所有业务数据均通过 REST API 访问，地址由 AGENTBOARD_API_URL 配置
（默认 http://127.0.0.1:8000）。MCP 服务不直接连接数据库。

运行：python -m agentboard.mcp_server   （stdio 传输）
"""
import os
from typing import Any
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
    # 作用域 = 令牌关联用户的权限（2026-07-29 修正）：
    # - 管理员身份 → /api/projects 全量视图（与 REST API 行为一致）；
    # - 普通用户 → /api/users/me/projects 成员作用域，防止越权浏览全部项目。
    # 防越权的正确边界是"给 MCP 配非管理员 key"（make-mcp-token.py 默认
    # mcp-service 用户），而不是在这里无视 is_admin 一刀切。
    params = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    me = _http("GET", "/api/auth/me")
    if isinstance(me, dict) and me.get("is_admin"):
        resp = _http("GET", "/api/projects", params=params)
    else:
        resp = _http("GET", "/api/users/me/projects", params=params)
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

def _story_create(epic_id, title, description, needs_design=True):
    return _http("POST", f"/api/epics/{epic_id}/stories",
                 json={"title": title, "description": description,
                       "needs_design": needs_design})

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

def _task_status(task_id, status, status_reason=None):
    body = {"status": status}
    if status_reason is not None:
        body["status_reason"] = status_reason
    return _http("PUT", f"/api/tasks/{task_id}/status", json=body)

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

def _story_comment_list(story_id):
    return _http("GET", f"/api/stories/{story_id}/comments")

def _story_comment_create(story_id, author, content):
    return _http("POST", f"/api/stories/{story_id}/comments",
                 json={"author": author, "content": content})

def _epic_comment_list(epic_id):
    return _http("GET", f"/api/epics/{epic_id}/comments")

def _epic_comment_create(epic_id, author, content):
    return _http("POST", f"/api/epics/{epic_id}/comments",
                 json={"author": author, "content": content})

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

def _schedule_create(project_id, title, schedule_type="cron", cron_expr=None,
                     agent=None, task_id=None, task_priority=None,
                     task_type=None, epic_id=None):
    body = {"title": title, "schedule_type": schedule_type}
    if cron_expr:
        body["cron_expr"] = cron_expr
    # Story 106：绑定松绑字段（None 不传 = 不设置）
    for k, v in dict(agent=agent, task_id=task_id, task_priority=task_priority,
                     task_type=task_type, epic_id=epic_id).items():
        if v is not None:
            body[k] = v
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
    """列出当前用户可见的项目（管理员可见全部；普通用户仅见自己创建或作为成员的项目）。limit / offset 用于分页。"""
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
def create_story(epic_id: int, title: str, description: str = "",
                 needs_design: bool = True) -> dict:
    """在指定 Epic 下创建 Story。
    - needs_design: 是否需要设计评审段（true=走 in_design→design_pending_review→design_review_approved；
      false=todo 直接进 in_progress 快速流），默认 true。
    """
    return _story_create(epic_id, title, description, needs_design)


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
                status: str | None = None, needs_design: bool | None = None) -> dict:
    """更新 Story 标题/描述/状态/needs_design（状态走强制迁移，非法会收到 400）。"""
    fields = {k: v for k, v in dict(title=title, description=description, status=status,
                                    needs_design=needs_design).items() if v is not None}
    return _story_update(story_id, fields)


@mcp.tool()
def confirm_story(story_id: int) -> dict:
    """用户确认 Story 开始（Ticket 全流程人工闸门）：backlog → confirmed。

    确认后触发 Agent 自动处理编排（design → 开发 → 评审 → 测试），
    由 Worker 周期拉起 agent 推进其下任务。
    """
    return _http("POST", f"/api/stories/{story_id}/confirm")


@mcp.tool()
def story_status_history(story_id: int, limit: int = 100) -> dict:
    """Story 状态变更历史（Ticket 全流程），按时间倒序。"""
    return _http("GET", f"/api/stories/{story_id}/status-history",
                 params={"limit": limit})


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
def set_status(task_id: int, status: str, status_reason: str | None = None) -> dict:
    """变更任务状态（校验合法迁移，见文档 FR-5；Story 265 后 done/blocked 必填 status_reason）。

    status_reason 可选值：
    - done: completed / withdrawn
    - blocked: blocked_by_other_ticket / pending_requirement_change / out_of_scope / duplicate
    """
    return _task_status(task_id, status, status_reason=status_reason)


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
def list_story_comments(story_id: int) -> list | dict:
    """按时间顺序读取 Story 评论，供人类与开发 Agent 共享进展。"""
    return _story_comment_list(story_id)


@mcp.tool()
def add_story_comment(story_id: int, author: str, content: str) -> dict:
    """给 Story 追加 markdown 评论；Agent 可用它同步开始、阻塞和完成状态。"""
    return _story_comment_create(story_id, author, content)


@mcp.tool()
def list_epic_comments(epic_id: int) -> list | dict:
    """按时间顺序读取 Epic 评论，供人类与开发 Agent 共享进展。"""
    return _epic_comment_list(epic_id)


@mcp.tool()
def add_epic_comment(epic_id: int, author: str, content: str) -> dict:
    """给 Epic 追加 markdown 评论；Agent 可用它同步开始、阻塞和完成状态。"""
    return _epic_comment_create(epic_id, author, content)


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
                    cron_expr: str | None = None, agent: str | None = None,
                    task_id: int | None = None, task_priority: str | None = None,
                    task_type: str | None = None, epic_id: int | None = None) -> dict:
    """创建定时计划。schedule_type: once/cron；cron_expr: 5 字段 cron 表达式。
    agent: 指定执行 Agent（codex/claude/workbuddy/qoder，None=默认）；
    task_id: 固定任务（旧单任务语义）；task_priority/task_type/epic_id: 可选筛选
    （项目/Agent 级 schedule 触发时自动挑 backlog/todo 中最高优先级 eligible task）。"""
    return _schedule_create(project_id, title, schedule_type=schedule_type,
                            cron_expr=cron_expr, agent=agent, task_id=task_id,
                            task_priority=task_priority, task_type=task_type,
                            epic_id=epic_id)


@mcp.tool()
def update_schedule(schedule_id: int, title: str | None = None,
                    schedule_type: str | None = None, cron_expr: str | None = None,
                    enabled: bool | None = None, next_run_at: str | None = None,
                    agent: str | None = None, task_id: int | None = None,
                    task_priority: str | None = None, task_type: str | None = None,
                    epic_id: int | None = None) -> dict:
    """更新 AgentSchedule 配置。仅传入需要修改的字段。
    清除绑定/筛选：agent/task_priority/task_type 传空串 ""，task_id/epic_id 传 0。"""
    fields = {k: v for k, v in dict(title=title, schedule_type=schedule_type,
                                    cron_expr=cron_expr, enabled=enabled,
                                    next_run_at=next_run_at).items() if v is not None}
    # Story 106：可清除字段——"" 与 0 为「清除」哨兵（MCP 无法区分 None=未传）
    for k, v in dict(agent=agent, task_priority=task_priority,
                     task_type=task_type).items():
        if v is not None:
            fields[k] = v if v != "" else None
    for k, v in dict(task_id=task_id, epic_id=epic_id).items():
        if v is not None:
            fields[k] = v if v != 0 else None
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
def report_run_result(run_id: int, status: str, summary: str | None = None,
                      log_ref: str | None = None) -> dict:
    """Agent 主动报告一次运行（Run）的最终结果（Epic 78 Story 104）。

    比心跳/退出码更可靠：Agent 执行完毕后显式上报 success/failed，
    执行器据此 finalize run（落库 summary + log_ref + finished_at）。

    - status: 仅 success / failed / cancelled 为合法终态；
    - 仅 pending/running 可迁移到终态；终态不可再变（幂等重复上报同状态不报错）；
    - summary: 运行结果摘要（markdown，可选）；
    - log_ref: 日志/产物引用（如外部存储路径，可选）。
    """
    body = {"status": status}
    if summary is not None:
        body["summary"] = summary
    if log_ref is not None:
        body["log_ref"] = log_ref
    return _http("POST", f"/api/runs/{run_id}/report", json=body)


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


# ---------- Agent MCP 工具（Task 92 / Epic 118 并发护栏）----------
def _agent_claim_task(task_id, agent_name="agent"):
    """Agent 认领任务（Epic 118 并发护栏版）：
    - 任务非 backlog/todo（已被认领或已结束）→ 返回明确错误，不创建 Run、不改状态；
    - 同一 agent 对同一 task 已有 active Run（pending/running）→ 幂等复用；
    - 空闲任务 → 创建 Run 并推进 in_progress。
    """
    import uuid
    # 获取 task 详情
    t = _http("GET", f"/api/tasks/{task_id}")
    if "error" in t:
        return t
    status = t.get("status")
    # 并发护栏：任务已被认领（in_progress 等）或已结束（done）时拒绝重复认领，
    # 避免多 Agent 并行时重复创建 Run / 重复推进状态。
    if status not in ("backlog", "todo"):
        return {
            "error": f"task {task_id} already claimed or not claimable (status={status})",
            "task": t,
            "run": None,
        }
    # Run 幂等复用：同一 task 已有 active Run（pending/running）则复用，不新建
    runs = _http("GET", "/api/schedules/1/runs")
    if isinstance(runs, list):
        for r in runs:
            if r.get("task_id") == task_id and r.get("status") in ("pending", "running"):
                _http("PUT", f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
                t = _http("GET", f"/api/tasks/{task_id}")
                return {"run": r, "task": t, "schedule": None, "reused": True}
    # 创建 run（schedule 1 为手动触发占位，历史约定保持不变）
    idempotency_key = f"{agent_name}-{task_id}-{uuid.uuid4().hex[:8]}"
    run = _http("POST", "/api/schedules/1/runs",
                json={"task_id": task_id, "idempotency_key": idempotency_key})
    if "error" in run:
        return {"error": run["error"], "task": t, "run": None}
    # 同步任务状态
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
def sync_status(task_id: int, status: str, comment: str | None = None,
                status_reason: str | None = None) -> dict:
    """同步任务状态，可选追加评论。
    status 必须符合状态机合法迁移规则（Story 265 后 done/blocked 必填 status_reason）。
    """
    result = _task_status(task_id, status, status_reason=status_reason)
    if "error" in result:
        return result
    if comment:
        # 获取 task 详情以确定 project 用于评论 author
        t = _task_get(task_id)
        author = t.get("spec", "").split("\n")[0][:50] if t else "agent"
        _comment_create(task_id, author=author, content=comment)
    return result


# ---------- Epic 20: Batch Operations ----------
@mcp.tool()
def batch_update_task_status(task_ids: list[int], new_status: str,
                              status_reason: str | None = None) -> dict:
    """批量更新任务状态（Story 265 后状态收敛为 5 值）。
    - task_ids: 任务 ID 列表（最多 100 个）
    - new_status: 新状态（todo/in_progress/in_review/done/blocked）
    - status_reason: 状态原因（done/blocked 必填，其他可选）
    """
    payload: dict = {"task_ids": task_ids, "status": new_status}
    if status_reason is not None:
        payload["status_reason"] = status_reason
    return _http("POST", "/api/tasks/bulk-update", json=payload)


@mcp.tool()
def batch_assign_sprint(task_ids: list[int], sprint_id: int | None) -> dict:
    """批量分配 Sprint。
    - task_ids: 任务 ID 列表（最多 100 个）
    - sprint_id: Sprint ID（设为 null 可移除分配）
    """
    payload = {"task_ids": task_ids, "sprint_id": sprint_id}
    return _http("POST", "/api/tasks/bulk-update", json=payload)


@mcp.tool()
def batch_delete_tasks(task_ids: list[int]) -> dict:
    """批量删除任务。
    - task_ids: 任务 ID 列表（最多 100 个）
    """
    return _http("POST", "/api/tasks/bulk-delete", json={"task_ids": task_ids})


# ---------- Epic 20: Enhanced Search ----------
@mcp.tool()
def search_tasks_enhanced(
    project_id: int | None = None,
    epic_id: int | None = None,
    story_id: int | None = None,
    sprint_id: int | None = None,
    type: str | None = None,
    status: str | list[str] | None = None,
    priority: str | list[str] | None = None,
    q: str | None = None,
    sort_by: str = "id",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """增强搜索任务：支持多值过滤和排序。
    - status/priority 可以是单个字符串或字符串列表（多值 OR 过滤）
    - sort_by: id, created_at, updated_at, priority, status, title
    - sort_order: asc, desc
    """
    params = {
        "sort_by": sort_by, "sort_order": sort_order,
        "limit": limit, "offset": offset,
    }
    if project_id is not None:
        params["project_id"] = project_id
    if epic_id is not None:
        params["epic_id"] = epic_id
    if story_id is not None:
        params["story_id"] = story_id
    if sprint_id is not None:
        params["sprint_id"] = sprint_id
    if type is not None:
        params["type"] = type
    # 多值过滤：httpx 会把 list 值自动展开成重复查询参数
    # （status=todo&status=in_progress），与 FastAPI 的 list[str] 声明对齐。
    # 传单值 str 时保持原样，两种形式后端都能解析。
    if status is not None:
        params["status"] = status
    if priority is not None:
        params["priority"] = priority
    if q is not None:
        params["q"] = q
    resp = _http("GET", "/api/tasks/search", params=params)
    if isinstance(resp, dict):
        # 后端分页信封 {"items": [...]} 或错误 {"error": ...}
        if "error" in resp:
            return []
        items = resp.get("items")
        return items if isinstance(items, list) else []
    return resp if isinstance(resp, list) else []


# ---------- Epic 20: Data Export ----------
@mcp.tool()
def export_project_data(project_id: int) -> dict:
    """导出项目完整数据（项目 + Epics + Stories + Tasks）。"""
    return _http("GET", f"/api/projects/{project_id}/export")


@mcp.tool()
def export_story_data(story_id: int) -> dict:
    """导出 Story 及所有子任务数据。"""
    return _http("GET", f"/api/stories/{story_id}/export")


# ---------- Epic 22 Story 22.1: 审计日志工具 ----------
@mcp.tool()
def list_audit_logs(
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出审计日志。
    - entity_type: project / epic / story / task
    - entity_id: 特定实体 ID
    - user_id: 特定用户 ID
    - action: GET / POST / PUT / PATCH / DELETE
    """
    params = {"limit": limit, "offset": offset}
    if entity_type:
        params["entity_type"] = entity_type
    if entity_id is not None:
        params["entity_id"] = entity_id
    if user_id is not None:
        params["user_id"] = user_id
    if action:
        params["action"] = action
    resp = _http("GET", "/api/audit-logs", params=params)
    return resp if isinstance(resp, dict) else {"items": resp}


# ---------- Epic 22 Story 22.2: 任务依赖关系工具 ----------
@mcp.tool()
def add_task_dependency(
    task_id: int,
    depends_on_id: int,
    dependency_type: str = "blocks",
) -> dict:
    """添加任务依赖关系。
    - task_id: 当前任务 ID
    - depends_on_id: 被依赖的任务 ID
    - dependency_type: blocks / blocked_by / relates_to
    """
    params = {"depends_on_id": depends_on_id, "dependency_type": dependency_type}
    resp = _http("POST", f"/api/tasks/{task_id}/dependencies", params=params)
    return resp


@mcp.tool()
def get_task_dependencies(task_id: int) -> dict:
    """获取任务的依赖关系（blockers 阻塞当前任务的 + blocked_by 被当前任务阻塞的）。"""
    resp = _http("GET", f"/api/tasks/{task_id}/dependencies")
    return resp if isinstance(resp, dict) else {"blockers": [], "blocked_by": []}


@mcp.tool()
def remove_task_dependency(dependency_id: int) -> dict:
    """删除依赖关系。"""
    resp = _http("DELETE", f"/api/dependencies/{dependency_id}")
    return resp if isinstance(resp, dict) else {"ok": True}


# ---------- Epic 22 Story 22.3: 导入工具 ----------
@mcp.tool()
def import_tasks(project_id: int, tasks_data: list[dict]) -> dict:
    """从 JSON 数据批量导入任务。
    - project_id: 目标项目 ID
    - tasks_data: 任务数据列表，每个元素包含 title/type/description/priority/status
    """
    resp = _http("POST", f"/api/projects/{project_id}/import", json={"tasks": tasks_data})
    return resp


# ---------- Epic 22 Story 22.4: Webhook 工具 ----------
@mcp.tool()
def create_webhook(
    name: str,
    url: str,
    project_id: int | None = None,
    secret: str | None = None,
    events: list[str] | None = None,
) -> dict:
    """创建 Webhook 配置。
    - name: Webhook 名称
    - url: Webhook 回调 URL
    - project_id: 可选，关联项目
    - secret: 可选，签名密钥
    - events: 监听事件列表，如 ["task.created","task.status_changed"]
    """
    params = {}
    if project_id is not None:
        params["project_id"] = project_id
    payload = {"name": name, "url": url}
    if secret:
        payload["secret"] = secret
    if events:
        payload["events"] = events
    resp = _http("POST", "/api/webhooks", params=params, json=payload)
    return resp


@mcp.tool()
def list_webhooks(project_id: int | None = None) -> dict:
    """列出 Webhook 配置。"""
    params = {}
    if project_id is not None:
        params["project_id"] = project_id
    resp = _http("GET", "/api/webhooks", params=params)
    return resp if isinstance(resp, dict) else {"items": resp}


@mcp.tool()
def delete_webhook(webhook_id: int) -> dict:
    """删除 Webhook 配置。"""
    resp = _http("DELETE", f"/api/webhooks/{webhook_id}")
    return resp if isinstance(resp, dict) else {"ok": True}


@mcp.tool()
def toggle_webhook(webhook_id: int, enabled: bool) -> dict:
    """启用/停用 Webhook。"""
    resp = _http("PATCH", f"/api/webhooks/{webhook_id}", params={"enabled": enabled})
    return resp


# ---------- Epic 13 / Story 32: 成员管理工具 ----------
def _member_list(project_id: int, limit: int = 50, offset: int = 0):
    params = {"limit": limit, "offset": offset}
    return _http("GET", f"/api/projects/{project_id}/members", params=params)

def _member_add(project_id: int, user_id: int | None = None, username: str | None = None, role: str = "member"):
    body = {"role": role}
    if user_id is not None:
        body["user_id"] = user_id
    if username is not None:
        body["username"] = username
    return _http("POST", f"/api/projects/{project_id}/members", json=body)

def _member_remove(project_id: int, user_id: int):
    return _http("DELETE", f"/api/projects/{project_id}/members/{user_id}")

def _member_update_role(project_id: int, user_id: int, role: str):
    return _http("PATCH", f"/api/projects/{project_id}/members/{user_id}", json={"role": role})


@mcp.tool()
def list_members(project_id: int, limit: int = 50, offset: int = 0) -> list:
    """列出项目成员（含 username）。project_id 必填。"""
    resp = _member_list(project_id, limit=limit, offset=offset)
    return resp.get("items", resp) if isinstance(resp, dict) else resp


@mcp.tool()
def add_member(project_id: int, user_id: int | None = None, username: str | None = None, role: str = "member") -> dict:
    """邀请用户加入项目（需 owner 或管理员权限）。
    - user_id 或 username 二选一
    - role: member / admin
    """
    return _member_add(project_id, user_id=user_id, username=username, role=role)


@mcp.tool()
def remove_member(project_id: int, user_id: int) -> dict:
    """移除项目成员（需 owner 或管理员权限，owner 不能移除自己）。"""
    return _member_remove(project_id, user_id)


@mcp.tool()
def update_member_role(project_id: int, user_id: int, role: str) -> dict:
    """更新成员角色（需 owner 或管理员权限）。role: member / admin"""
    return _member_update_role(project_id, user_id, role)


# ---------- Epic 13 / Story 32: 通知工具 ----------
def _notification_list(limit: int = 20, offset: int = 0, unread_only: bool = False):
    params = {"limit": limit, "offset": offset, "unread_only": unread_only}
    return _http("GET", "/api/notifications", params=params)

def _notification_unread_count():
    return _http("GET", "/api/notifications/unread-count")

def _notification_mark_read(notification_id: int):
    return _http("POST", f"/api/notifications/{notification_id}/read")

def _notification_mark_all_read():
    return _http("POST", "/api/notifications/read-all")

def _notification_delete(notification_id: int):
    return _http("DELETE", f"/api/notifications/{notification_id}")


@mcp.tool()
def list_notifications(limit: int = 20, offset: int = 0, unread_only: bool = False) -> dict:
    """列出当前用户通知。unread_only=True 仅返回未读。"""
    resp = _notification_list(limit=limit, offset=offset, unread_only=unread_only)
    return resp if isinstance(resp, dict) else {"items": resp}


@mcp.tool()
def notification_unread_count() -> dict:
    """返回当前用户未读通知数量。"""
    return _notification_unread_count()


@mcp.tool()
def mark_notification_read(notification_id: int) -> dict:
    """标记单条通知为已读。"""
    return _notification_mark_read(notification_id)


@mcp.tool()
def mark_all_notifications_read() -> dict:
    """标记当前用户全部通知为已读。"""
    return _notification_mark_all_read()


@mcp.tool()
def delete_notification(notification_id: int) -> dict:
    """删除单条通知。"""
    return _notification_delete(notification_id)


# ---------- Epic 13 / Story 32: 管理员工具 ----------
def _admin_list_users(limit: int = 50, offset: int = 0):
    return _http("GET", "/api/admin/users", params={"limit": limit, "offset": offset})

def _admin_update_user(user_id: int, is_admin: bool):
    return _http("PATCH", f"/api/admin/users/{user_id}", json={"is_admin": is_admin})

def _admin_list_projects(limit: int = 50, offset: int = 0):
    return _http("GET", "/api/admin/projects", params={"limit": limit, "offset": offset})

def _admin_delete_project(project_id: int):
    return _http("DELETE", f"/api/admin/projects/{project_id}")


@mcp.tool()
def admin_list_users(limit: int = 50, offset: int = 0) -> dict:
    """（管理员）列出全部用户。"""
    resp = _admin_list_users(limit=limit, offset=offset)
    return resp if isinstance(resp, dict) else {"items": resp}


@mcp.tool()
def admin_set_user_admin(user_id: int, is_admin: bool) -> dict:
    """（管理员）设置/取消用户管理员权限。is_admin: true / false"""
    return _admin_update_user(user_id, is_admin)


@mcp.tool()
def admin_list_projects(limit: int = 50, offset: int = 0) -> dict:
    """（管理员）列出全部项目。"""
    resp = _admin_list_projects(limit=limit, offset=offset)
    return resp if isinstance(resp, dict) else {"items": resp}


@mcp.tool()
def admin_delete_project(project_id: int) -> dict:
    """（管理员）删除项目（危险操作，级联删除其下全部数据）。"""
    return _admin_delete_project(project_id)


# ---------- Documents MCP 工具（Epic 15：项目文档维护）----------
def _doc_create(project_id, title, content="", type="plan", status="draft",
                epic_id=None, story_id=None, author_id=None, folder_id=None):
    body = {"project_id": project_id, "title": title, "content": content,
            "type": type, "status": status}
    if epic_id is not None:
        body["epic_id"] = epic_id
    if story_id is not None:
        body["story_id"] = story_id
    if author_id is not None:
        body["author_id"] = author_id
    if folder_id is not None:
        body["folder_id"] = folder_id
    return _http("POST", "/api/documents", json=body)


def _doc_get(document_id):
    return _http("GET", f"/api/documents/{document_id}")


def _doc_list(project_id=None, type=None, status=None, q=None, limit=None, offset=0,
              folder_id=None, author_id=None, epic_id=None, story_id=None, sort=None):
    params = {"offset": offset}
    if project_id is not None:
        params["project_id"] = project_id
    if type is not None:
        params["type"] = type
    if status is not None:
        params["status"] = status
    if q is not None:
        params["q"] = q
    if folder_id is not None:
        params["folder_id"] = folder_id
    if author_id is not None:
        params["author_id"] = author_id
    if epic_id is not None:
        params["epic_id"] = epic_id
    if story_id is not None:
        params["story_id"] = story_id
    if sort is not None:
        params["sort"] = sort
    if limit is not None:
        params["limit"] = limit
    return _http("GET", "/api/documents", params=params)


def _doc_update(document_id, fields):
    return _http("PATCH", f"/api/documents/{document_id}", json=fields)


def _doc_delete(document_id):
    return _http("DELETE", f"/api/documents/{document_id}")


def _doc_status(document_id, status):
    return _http("PUT", f"/api/documents/{document_id}/status", json={"status": status})


def _doc_comment_create(document_id, author, content, author_id=None):
    body = {"author": author, "content": content}
    if author_id is not None:
        body["author_id"] = author_id
    return _http("POST", f"/api/documents/{document_id}/comments", json=body)


def _doc_comment_list(document_id):
    return _http("GET", f"/api/documents/{document_id}/comments")


def _doc_comment_update(comment_id, content, author):
    return _http("PATCH", f"/api/document-comments/{comment_id}",
                 json={"content": content, "author": author})


def _doc_comment_delete(comment_id):
    return _http("DELETE", f"/api/document-comments/{comment_id}")


@mcp.tool()
def create_document(project_id: int, title: str, content: str = "",
                   type: str = "plan", status: str = "draft",
                   epic_id: int | None = None, story_id: int | None = None,
                   author_id: int | None = None, folder_id: int | None = None) -> dict:
    """新建文档。type: memory/plan/knowledge/design；status 默认 draft；folder_id 指定所属文件夹。"""
    return _doc_create(project_id, title, content=content, type=type, status=status,
                       epic_id=epic_id, story_id=story_id, author_id=author_id,
                       folder_id=folder_id)


@mcp.tool()
def get_document(document_id: int) -> dict:
    """获取文档详情（含 title / content / type / status）。"""
    return _doc_get(document_id)


@mcp.tool()
def list_documents(project_id: int | None = None, type: str | None = None,
                  status: str | None = None, q: str | None = None,
                  folder_id: int | None = None, author_id: int | None = None,
                  epic_id: int | None = None, story_id: int | None = None,
                  sort: str | None = None,
                  limit: int = 100, offset: int = 0) -> list:
    """按丰富条件过滤列出文档。sort ∈ {updated, created, title}（默认 updated 倒序）。返回文档列表。"""
    return _doc_list(project_id=project_id, type=type, status=status, q=q,
                     folder_id=folder_id, author_id=author_id, epic_id=epic_id,
                     story_id=story_id, sort=sort, limit=limit, offset=offset)


@mcp.tool()
def count_document_comments(document_id: int) -> dict:
    """返回指定文档的评论总数。供列表视图按需并发取数。文档不存在返回错误。"""
    return _http("GET", f"/api/documents/{document_id}/comments/count")


@mcp.tool()
def update_document(document_id: int, title: str | None = None,
                   content: str | None = None, type: str | None = None,
                   folder_id: int | None = None,
                   remove_from_folder: bool = False) -> dict:
    """编辑文档标题 / 正文 / 类型 / 所属文件夹。仅传入需要修改的字段；
    folder_id 移动到指定文件夹；remove_from_folder=true 表示移出到根目录
    （与 folder_id 同时传入时 remove_from_folder 优先）。"""
    fields = {k: v for k, v in dict(title=title, content=content, type=type).items() if v is not None}
    if remove_from_folder:
        fields["folder_id"] = None
    elif folder_id is not None:
        fields["folder_id"] = folder_id
    return _doc_update(document_id, fields)


@mcp.tool()
def delete_document(document_id: int) -> dict:
    """删除文档（级联删除其评论）。"""
    return _doc_delete(document_id)


# ---------- 文档文件夹 MCP 工具（Epic 15 增强：文件夹/子文件夹）----------
def _folder_list(project_id=None):
    params = {}
    if project_id is not None:
        params["project_id"] = project_id
    return _http("GET", "/api/document-folders", params=params)


def _folder_create(project_id, name, parent_id=None):
    body = {"project_id": project_id, "name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    return _http("POST", "/api/document-folders", json=body)


def _folder_update(folder_id, fields):
    return _http("PATCH", f"/api/document-folders/{folder_id}", json=fields)


def _folder_delete(folder_id):
    return _http("DELETE", f"/api/document-folders/{folder_id}")


@mcp.tool()
def create_document_folder(project_id: int, name: str,
                           parent_id: int | None = None) -> dict:
    """创建文档文件夹（支持 parent_id 建子文件夹；不传则建在根目录）。"""
    return _folder_create(project_id, name, parent_id=parent_id)


@mcp.tool()
def list_document_folders(project_id: int | None = None) -> list:
    """列出文档文件夹树（可按 project_id 过滤）。"""
    return _folder_list(project_id=project_id)


@mcp.tool()
def update_document_folder(folder_id: int, name: str | None = None,
                           parent_id: int | None = None,
                           move_to_root: bool = False) -> dict:
    """重命名 / 移动文件夹。parent_id 移到指定父级；move_to_root=true 表示
    移到根目录（与 parent_id 同时传入时 move_to_root 优先）。"""
    fields = {}
    if name is not None:
        fields["name"] = name
    if move_to_root:
        fields["parent_id"] = None
    elif parent_id is not None:
        fields["parent_id"] = parent_id
    return _folder_update(folder_id, fields)


@mcp.tool()
def delete_document_folder(folder_id: int) -> dict:
    """删除文件夹（其下文档上提父级，不删除文档本身）。"""
    return _folder_delete(folder_id)


@mcp.tool()
def set_document_status(document_id: int, status: str) -> dict:
    """文档评审状态流转：draft→in_review→approved/cancelled/draft；approved→draft。"""
    return _doc_status(document_id, status)


@mcp.tool()
def add_document_comment(document_id: int, author: str, content: str,
                         author_id: int | None = None) -> dict:
    """对文档追加 markdown 评论；author 为成员或 Agent 账号名。"""
    return _doc_comment_create(document_id, author, content, author_id=author_id)


@mcp.tool()
def list_document_comments(document_id: int) -> list | dict:
    """按时间顺序读取文档评论，供多成员 / 多 Agent 互相 review。"""
    return _doc_comment_list(document_id)


@mcp.tool()
def update_document_comment(comment_id: int, content: str, author: str) -> dict:
    """编辑文档评论：仅作者（成员或 Agent 账号）可编辑自己的评论。"""
    return _doc_comment_update(comment_id, content, author)


@mcp.tool()
def delete_document_comment(comment_id: int) -> dict:
    """删除指定文档评论。"""
    return _doc_comment_delete(comment_id)


@mcp.tool()
def search_documents(project_id: int | None = None, q: str | None = None,
                     type: str | None = None, status: str | None = None,
                     limit: int = 100, offset: int = 0) -> list:
    """关键词搜索文档（匹配 title / content）。可按 type / status 进一步过滤。"""
    return _doc_list(project_id=project_id, q=q, type=type, status=status,
                     limit=limit, offset=offset)


# ===================== Agent 记忆（Epic 78 Story 107：跨会话大脑） =====================
#
# 把 Document.type=memory 文本载体升维为 Agent 的跨会话记忆：Agent 每次会话启动时
# 调 get_project_memory 自动加载项目记忆；会话中把新学到的约定 / 踩坑用
# append_agent_memory 沉淀回去，同一项目多次会话间记忆可累积复用（对标 Mem0 / Zep，
# 但长在 PM 里、与任务闭环打通）。
#
# 分层约定（零 DB 变更，title 前缀隔离）：
#   - 项目级：title = "项目记忆" —— 团队规范 / 约定 / 踩坑，所有 Agent 共享；
#   - Agent 级：title = "Agent 记忆 · {agent}" —— 某 Agent 个性 / 擅长领域，按 agent 隔离。

_MEMORY_PROJECT_TITLE = "项目记忆"
_MEMORY_AGENT_PREFIX = "Agent 记忆 · "


def _memory_title(agent: str | None) -> str:
    return f"{_MEMORY_AGENT_PREFIX}{agent}" if agent else _MEMORY_PROJECT_TITLE


@mcp.tool()
def get_project_memory(project_id: int, agent: str | None = None) -> dict:
    """读取项目记忆（Document.type=memory，跨会话累积）。

    agent 为空返回项目全部 memory 文档；给定 agent 时仅返回「项目级记忆 + 该
    Agent 专属记忆」（Agent 级隔离）。返回 documents 列表 + combined 拼接文本，
    供 Agent 会话启动时一次自动加载。
    """
    docs = _doc_list(project_id=project_id, type="memory", limit=100)
    if not isinstance(docs, list):
        return {"project_id": project_id, "agent": agent,
                "documents": [], "combined": ""}
    if agent:
        docs = [d for d in docs
                if d.get("title") == _MEMORY_PROJECT_TITLE
                or d.get("title") == _memory_title(agent)]
    for d in docs:
        d["content"] = d.get("content") or ""
    combined = "\n\n".join(
        f"[{d.get('title', '')}]\n{d.get('content', '')}" for d in docs)
    return {"project_id": project_id, "agent": agent,
            "documents": docs, "combined": combined}


@mcp.tool()
def append_agent_memory(project_id: int, content: str, agent: str | None = None) -> dict:
    """向项目记忆追加一段内容（幂等累积：同名记忆文档续写而非新建）。

    agent 为空 → 项目级记忆（title=项目记忆）；给定 agent → 该 Agent 专属记忆
    （title=Agent 记忆 · {agent}，与其他 Agent 隔离）。返回目标文档 id 与结果。
    """
    title = _memory_title(agent)
    docs = _doc_list(project_id=project_id, type="memory", limit=100)
    target = None
    if isinstance(docs, list):
        target = next((d for d in docs if d.get("title") == title), None)
    if target is not None:
        old = target.get("content") or ""
        merged = (old + "\n\n" + content).strip()
        _doc_update(target["id"], {"content": merged})
        return {"document_id": target["id"], "title": title, "appended": True,
                "content_length": len(merged)}
    created = _doc_create(project_id, title, content=content, type="memory")
    return {"document_id": created.get("id") or created.get("document_id"),
            "title": title, "appended": False,
            "content_length": len(content)}


# ===================== Proposals（Epic 96 P1-1：澄清回路 Worker 侧工具面） =====================
#
# Epic 96 P0 已交付完整 REST 层与前端问答工作台，但无头 Agent / Worker 侧此前没有任何入口。
# 本组工具让 Worker 能完整跑通澄清回路：
#
#     proposal_pending → proposal_claim → proposal_get（全量重放）
#                              → proposal_ask（回写一轮问题，等用户作答）
#                              → proposal_get（含历史答案）→ … 多轮收敛 …
#                              → proposal_finalize / proposal_fail
#
# 会话续接采用**全量重放**（Story 155 设计）：不保存 Agent 侧会话，每轮把
# 原始需求 + 全量历史问答重新拼进上下文重跑，天然幂等、可横向扩容、崩溃可恢复。

def _is_http_error(resp) -> bool:
    """判定 `_http` 是否返回了传输层错误。

    不能简单用 ``"error" in resp``——提案实体自身就带一个 ``error`` 字段
    （status=failed 时的失败原因），任何正常的提案 dict 都含该键。
    `_http` 失败时返回的恰好是**只有 error 一个键**的 dict，据此精确区分。
    """
    return isinstance(resp, dict) and set(resp.keys()) == {"error"}


def _proposal_status(proposal_id: int, status: str, error: str | None = None) -> dict:
    """提案状态机流转（私有 helper，供 claim / finalize / fail 复用）。"""
    body: dict = {"status": status}
    if error is not None:
        body["error"] = error
    return _http("PUT", f"/api/proposals/{proposal_id}/status", json=body)


def _proposal_replay(proposal: dict, rounds: list) -> dict:
    """把提案正文与全部历史轮次压成一份可直接重放的上下文。

    ``history`` 为按轮次正序的扁平问答（含 unsure 标记），Agent 只要读这一份
    就能无状态地续接澄清——这正是全量重放策略的落点。
    ``open_questions`` 单独列出尚未作答的问题，便于 Agent 判断是否还在等人。
    """
    history: list[dict] = []
    open_questions: list[dict] = []
    for r in rounds or []:
        for q in r.get("questions", []) or []:
            answered = bool(q.get("answered_at"))
            item = {
                "round": r.get("round_no"),
                "question_id": q.get("id"),
                "seq": q.get("seq"),
                "question": q.get("question"),
                "answer": q.get("answer") or "",
                "unsure": bool(q.get("unsure")),
                "answered": answered,
            }
            history.append(item)
            if not answered:
                open_questions.append(item)
    return {
        "proposal_id": proposal.get("id"),
        "project_id": proposal.get("project_id"),
        "title": proposal.get("title"),
        "content": proposal.get("content") or "",
        "status": proposal.get("status"),
        "current_round": proposal.get("current_round", 0),
        "converged_spec": proposal.get("converged_spec") or "",
        "error": proposal.get("error") or "",
        "rounds": rounds or [],
        "history": history,
        "open_questions": open_questions,
        "answered_count": sum(1 for h in history if h["answered"]),
        "total_questions": len(history),
    }


@mcp.tool()
def proposal_pending(limit: int = 20) -> list | dict:
    """轮询待认领的需求提案（status=queued），供 Worker 领取澄清任务。

    P1 先用 DB 轮询，P2 由 RabbitMQ 替换；返回顺序为 updated_at 倒序。
    """
    return _http("GET", "/api/proposals/pending", params={"limit": limit})


@mcp.tool()
def proposal_claim(proposal_id: int, agent: str = "") -> dict:
    """**原子**认领一个待处理提案：queued/answered → analyzing。

    已被其他 Worker 认领（或状态不可认领）时返回明确 error，绝不静默成功，
    避免多个 Worker 对同一提案重复分析。``agent`` 为 Worker 服务账号名，
    会记入租约并在后续 proposal_ask 时落到轮次记录上。

    走服务端 CAS 端点 ``POST /api/proposals/{id}/claim``：判定与写入压在单条条件
    UPDATE 内由数据库仲裁，无 TOCTOU 窗口。不要改回「先 GET 查状态再 PUT
    /status」——状态机对同状态迁移是幂等 no-op（返回 200），PUT 本身不具备仲裁
    能力，并发下多个 Worker 会同时「认领成功」。

    ``answered`` 同样可认领：用户作答后需由 Worker 接手进入下一轮澄清，该语义与
    worker.py 的 CLAIMABLE_STATUSES 对齐。
    """
    r = _http("POST", f"/api/proposals/{proposal_id}/claim", json={"agent": agent})
    if _is_http_error(r):
        return r
    return {"ok": True, "claimed_by": agent, "proposal": r}


@mcp.tool()
def proposal_get(proposal_id: int) -> dict:
    """拉取提案的**全量重放上下文**：原始需求正文 + 全部历史轮次问答。

    返回 ``history``（扁平问答，含 answer 与 unsure 标记）与 ``open_questions``
    （尚未作答的问题）。Agent 每轮只需调用本工具一次即可无状态续接澄清，
    无需在本地保存任何会话——崩溃后重跑结果一致。
    """
    p = _http("GET", f"/api/proposals/{proposal_id}")
    if _is_http_error(p):
        return p
    rounds = _http("GET", f"/api/proposals/{proposal_id}/rounds")
    if _is_http_error(rounds):
        return rounds
    return _proposal_replay(p, rounds)


@mcp.tool()
def proposal_ask(proposal_id: int, questions: list, round: int | None = None,
                 summary: str = "", agent: str = "") -> dict:
    """回写一轮 open questions，并把提案推进到 awaiting 等待用户作答。

    ``round`` 省略时自动取下一轮。同一 (proposal, round) 重复提交会幂等复用
    既有轮次，不产生重复问题——兜底消息 at-least-once 重投与 LLM 非确定性。
    """
    body: dict = {"questions": list(questions), "summary": summary, "agent": agent}
    if round is not None:
        body["round"] = round
    return _http("POST", f"/api/proposals/{proposal_id}/questions", json=body)


@mcp.tool()
def proposal_finalize(proposal_id: int, converged_spec: str) -> dict:
    """澄清收敛：写入最终需求规格并推进到 converged，等待人工终审转 Story。

    保留人类最后一道闸——本工具不直接创建 Story（P3 由服务端在终审后转化）。
    """
    if not (converged_spec or "").strip():
        return {"error": "converged_spec 不能为空：收敛定稿必须给出最终需求规格"}
    r = _http("PATCH", f"/api/proposals/{proposal_id}",
              json={"converged_spec": converged_spec})
    if _is_http_error(r):
        return r
    return _proposal_status(proposal_id, "converged")


@mcp.tool()
def proposal_fail(proposal_id: int, error: str) -> dict:
    """标记提案分析失败并记录原因，供后续回退 queued 重投。"""
    return _proposal_status(proposal_id, "failed", error=error or "unspecified failure")


@mcp.tool()
def proposal_convert(proposal_id: int, epic_id: int, title: str | None = None) -> dict:
    """人工终审确认：把已收敛（converged）提案转化为 Story + 子 Task（Epic 96 P3）。

    保留人类最后一道闸 —— 本工具不直接 create_story，而是经服务端转化端点
    ``POST /api/proposals/{pid}/convert`` 完成：基于 converged_spec 生成 Story
    （description 存原文）与子 Task（``- [ ]`` 清单项），回填 proposal.story_id
    并推进 converged → story_created。幂等：重复调用返回既有 Story，不重复创建。

    epic_id 必填，且必须属于提案所在项目；title 可覆盖 Story 标题（省略用提案标题）。
    仅人工/管理员终审时调用。
    """
    body = {"epic_id": epic_id}
    if title:
        body["title"] = title
    return _http("POST", f"/api/proposals/{proposal_id}/convert", json=body)


@mcp.tool()
def proposal_create_ticket(proposal_id: int, type: str,
                           epic_id: int | None = None,
                           story_id: int | None = None,
                           title: str | None = None) -> dict:
    """把已收敛（converged）提案异步生成为工单（2026-08-08 文档 #59）。

    type 四选一：epic / story / task / bug —— 层级约束：
    - epic 独立，无需父级；
    - story 必填 epic_id；
    - task / bug 必填 epic_id + story_id（复用 tasks 表，type 区分 bug）。

    服务端事务内完成：层级校验 + 创建实体 + 回填 proposal.ticket_type/ticket_id
    + 状态推进 converged → ticket_preparing → ticket_created；幂等：重复调用
    返回既有结果（不重复创建）。调用方通常是 worker CLI 拉起的 agent。
    """
    body: dict[str, Any] = {"proposal_id": proposal_id, "type": type}
    if epic_id is not None:
        body["epic_id"] = epic_id
    if story_id is not None:
        body["story_id"] = story_id
    if title:
        body["title"] = title
    return _http(
        "POST", "/api/ticket-requests:execute",
        json=body,
    )


# ---------- Epic 122 S1 M3: Agent 注册 / 评审闭环 MCP 工具 ----------
@mcp.tool()
def agent_register(agent_id: str, name: str, roles: str = "[]",
                   capabilities: str = "[]", cli_command: str = "",
                   model: str = "", auth_key: str = "") -> dict:
    """注册/更新 Agent 身份（幂等）。

    - agent_id: 外部 Agent 自报唯一标识（幂等键）
    - name: 显示名
    - roles: JSON 数组串，如 ``["reviewer","developer"]``（评审分配按 role 过滤）
    - capabilities: JSON 数组串能力标签
    - cli_command: CLI 拉起命令模板（支持 ``{model}`` 占位，worker probe 会注入模型）
    - model: 模型名（如 hy3 / deepseek-v4-flash / MiniMax-M2；同 CLI 多 agent 各配模型）
    - auth_key: 绑定 abk_ key 指纹（可选）
    注册即绑定当前 MCP 身份对应的服务账号（AgentBoard 用户）。
    """
    return _http("POST", "/api/agents/register", json={
        "agent_id": agent_id, "name": name, "roles": roles,
        "capabilities": capabilities, "cli_command": cli_command,
        "model": model, "auth_key": auth_key,
    })


@mcp.tool()
def agent_heartbeat(agent_id: str) -> dict:
    """Agent 心跳保活：置 online=True 并刷新 last_heartbeat。

    在线 Agent 才会被随机评审分配器（assign-reviewer）选为候选。
    """
    return _http("POST", f"/api/agents/{agent_id}/heartbeat")


@mcp.tool()
def agent_deregister(agent_id: str) -> dict:
    """Agent 注销下线（保留注册记录，online=False）。仅 Agent 自身或 admin 可操作。"""
    return _http("POST", f"/api/agents/{agent_id}/deregister")


@mcp.tool()
def list_agents(online: bool | None = None, role: str | None = None) -> list | dict:
    """列出已注册 Agent。

    - online=true 只看在线；role=reviewer 只看含该角色的 Agent。
    """
    params: dict = {}
    if online is not None:
        params["online"] = "true" if online else "false"
    if role:
        params["role"] = role
    return _http("GET", "/api/agents", params=params)


@mcp.tool()
def review_story(story_id: int, verdict: str, comment: str) -> dict:
    """评审投票（approve/reject + 评论，CAS）：仅被指派 reviewer 可操作。

    - verdict: approve（通过，Story → ready）| reject（打回，评论往返收敛）
    - comment: 评审意见（必填，作为 Story 评论落库，是评审意见唯一载体）
    - reject 连续 5 轮未收敛 → Story 置 blocked 护栏（待人工仲裁）
    """
    return _http("POST", f"/api/stories/{story_id}/review",
                 json={"verdict": verdict, "comment": comment})


@mcp.tool()
def list_review_tasks(status: str | None = None) -> list | dict:
    """拉取指派给当前 Agent 的评审任务（Story 列表）。

    - status 可选：pending_review / ready / blocked（省略返回全部指派给我的）
    """
    params: dict = {"reviewer_id": "me"}
    if status:
        params["status"] = status
    return _http("GET", "/api/stories", params=params)


# ---------- Epic 122 切片 2 M1: 开发任务竞争认领 / 提交评审 ----------
@mcp.tool()
def claim_development_task(task_id: int) -> dict:
    """开发任务竞争认领（CAS 并发安全，恰一赢家）。

    - 任务状态为 backlog/todo → 置 in_progress 并回填 assignee=当前 Agent 绑定用户；
    - 已认领（in_progress/in_review 等）或已结束（done/blocked）→ 409 明确错误，不重复认领；
    - 配合 Story ready 后的 ``task.available`` 广播使用（开发者竞争认领同一任务）。
    """
    return _http("POST", f"/api/tasks/{task_id}/claim")


@mcp.tool()
def submit_task_for_review(task_id: int) -> dict:
    """开发完成提交评审（assignee 或 admin）→ 任务置 in_review 并广播 task.ready_for_review。

    提交后等待 Task reviewer 拉取任务 + 评论评审（review_task approve/reject）。
    """
    return _http("POST", f"/api/tasks/{task_id}/submit-review")


# ---------- Epic 122 切片 2 M2: Task 评审闭环 ----------
@mcp.tool()
def assign_task_reviewer(task_id: int) -> dict:
    """随机指派 Task 评审人（幂等，CAS 并发安全，切片 2 M2）。

    - 仅 in_review 任务可指派；候选 = 在线 reviewer ∩ 项目成员 ∩ ≠ assignee；
    - 已指派（reviewer_id 非空）直接返回现态，不换人；
    - 无在线 reviewer → 422，由开发者轮询 list_task_review_tasks 兜底。
    """
    return _http("POST", f"/api/tasks/{task_id}/assign-reviewer")


@mcp.tool()
def review_task(task_id: int, verdict: str, comment: str) -> dict:
    """Task 评审投票（approve/reject + 评论，CAS）：仅被指派 reviewer 可操作。

    - verdict: approve（通过，Task → done）| reject（打回，退回 in_progress，
      开发者修复后重新 submit-review，评审人保留）
    - comment: 评审意见（必填，作为 Task 评论落库，是评审意见唯一载体）
    - reject 连续 5 轮未收敛 → Task 置 blocked 护栏（待人工仲裁）
    """
    return _http("POST", f"/api/tasks/{task_id}/review",
                 json={"verdict": verdict, "comment": comment})


@mcp.tool()
def list_task_review_tasks(status: str | None = None) -> list | dict:
    """拉取指派给当前 Agent 的 Task 评审任务。

    - status 可选：in_review / done / blocked（省略返回全部指派给我的）
    """
    params: dict = {"reviewer_id": "me"}
    if status:
        params["status"] = status
    return _http("GET", "/api/tasks", params=params)


@mcp.tool()
def get_review_stats(project_id: int, days: int = 7, user_id: int | None = None) -> dict:
    """项目级评审统计运营视图（多 Agent 协作闭环 S3 M2）。

    - project_id: 必填，目标项目
    - days: 统计窗口天数（默认 7，0 = 全部历史）
    - user_id: 可选，只统计该评审人参与的条目

    返回：Story/Task 评审汇总（total/approved/rejected/pending/blocked）、
    平均轮次、驳回率、当前超时未决数、按 reviewer 聚合工作量。
    """
    params: dict = {"project_id": project_id, "days": days}
    if user_id is not None:
        params["user_id"] = user_id
    return _http("GET", "/api/review-stats", params=params)


@mcp.tool()
def scan_review_timeouts(project_id: int | None = None,
                         timeout_minutes: int = 30,
                         max_per_run: int = 20) -> dict:
    """评审超时自愈扫描（多 Agent 协作闭环 S3 M2 护栏）。

    - project_id: 可选（省略 = 全局扫描，Worker 场景）
    - timeout_minutes: 超时阈值（默认 30 分钟）
    - max_per_run: 单轮最多处理数（默认 20，防独占）

    处理：pending_review Story / in_review Task 且 reviewer 已指派且最后活动超时 →
    轮次已达 5 轮上限置 blocked（护栏终态）；否则解绑旧 reviewer 并重新随机指派
    （排除旧 reviewer；Task 额外排除 assignee）。返回重派/阻塞/无候选统计。
    """
    params: dict = {}
    if project_id is not None:
        params["project_id"] = project_id
    body: dict = {"timeout_minutes": timeout_minutes, "max_per_run": max_per_run}
    return _http("POST", "/api/review-stats/reassign-timeout",
                 params=params or None, json=body)


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
