"""AgentBoard MCP 服务（独立）。

后端可切换（前后端分离友好）：
- AGENTBOARD_MCP_BACKEND=api （默认）：通过 httpx 调用 REST API，地址 AGENTBOARD_API_URL（默认 http://127.0.0.1:8000）
- AGENTBOARD_MCP_BACKEND=db        ：直连数据库（复用 service 层，无需启动 API）

运行：python -m agentboard.mcp_server   （stdio 传输）
"""
import os
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.dependencies import get_access_token

from . import auth as agent_auth

BACKEND = os.getenv("AGENTBOARD_MCP_BACKEND", "api").lower()
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000")
MCP_REQUIRE_AUTH = os.getenv("AGENTBOARD_MCP_REQUIRE_AUTH", "1").lower() in {"1", "true", "yes"}


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


# ===================== 后端实现 =====================
if BACKEND == "db":
    from .database import SessionLocal
    from . import service

    def _proj_list(limit=None, offset=0):
        with SessionLocal() as s:
            return [service._ser(p) for p in service.list_projects(s, limit=limit, offset=offset)]

    def _proj_create(name, key, description):
        with SessionLocal() as s:
            return service._ser(service.create_project(s, name=name, key=key, description=description))

    def _proj_get(project_id):
        with SessionLocal() as s:
            p = service.get_project(s, project_id)
            return service._ser(p) if p else {"error": "not found"}

    def _proj_update(project_id, fields):
        with SessionLocal() as s:
            p = service.update_project(s, project_id, **fields)
            return service._ser(p) if p else {"error": "not found"}

    def _proj_delete(project_id):
        with SessionLocal() as s:
            return {"ok": service.delete_project(s, project_id)}

    def _epic_list(project_id, limit=None, offset=0):
        with SessionLocal() as s:
            return [service._ser(x) for x in service.list_epics(s, project_id, limit=limit, offset=offset)]

    def _epic_create(project_id, title, description):
        with SessionLocal() as s:
            return service._ser(service.create_epic(s, project_id=project_id, title=title, description=description))

    def _story_create(epic_id, title, description):
        with SessionLocal() as s:
            return service._ser(service.create_story(s, epic_id=epic_id, title=title, description=description))

    def _story_list(epic_id, limit=None, offset=0):
        with SessionLocal() as s:
            return [service._ser(x) for x in service.list_stories(s, epic_id, limit=limit, offset=offset)]

    def _task_list(story_id, limit=None, offset=0):
        with SessionLocal() as s:
            return [service._ser(x) for x in service.list_tasks(s, story_id, limit=limit, offset=offset)]

    def _task_create(project_id, story_id, title, type, description, spec, priority="medium"):
        with SessionLocal() as s:
            try:
                return service._ser(service.create_task(s, project_id=project_id, story_id=story_id,
                                                        title=title, type=type, description=description,
                                                        spec=spec, priority=priority))
            except service.InvalidValue as e:
                return {"error": str(e)}

    def _task_get(task_id):
        with SessionLocal() as s:
            t = service.get_task(s, task_id)
            return service._ser(t) if t else {"error": "not found"}

    def _task_update(task_id, fields):
        with SessionLocal() as s:
            try:
                t = service.update_task(s, task_id, **fields)
            except service.InvalidValue as e:
                return {"error": str(e)}
            return service._ser(t) if t else {"error": "not found"}

    def _task_append_spec(task_id, text):
        with SessionLocal() as s:
            t = service.append_task_spec(s, task_id, text)
            return service._ser(t) if t else {"error": "not found"}

    def _task_delete(task_id):
        with SessionLocal() as s:
            return {"ok": service.delete_task(s, task_id)}

    def _task_status(task_id, status):
        with SessionLocal() as s:
            try:
                return service._ser(service.set_status(s, task_id, status))
            except (service.NotFound, service.IllegalTransition) as e:
                return {"error": str(e)}

    def _task_search(params):
        with SessionLocal() as s:
            return [service._ser(t) for t in service.search_tasks(s, **params)]

    def _task_generated(task_id):
        with SessionLocal() as s:
            try:
                created = service.generate_tasks_from_spec(s, task_id)
            except service.NotFound as e:
                return {"error": str(e)}
            return [service._ser(t) for t in created]

    def _epic_get(epic_id):
        with SessionLocal() as s:
            e = service.get_epic(s, epic_id)
            return service._ser(e) if e else {"error": "not found"}

    def _epic_update(epic_id, fields):
        with SessionLocal() as s:
            e = service.update_epic(s, epic_id, **fields)
            return service._ser(e) if e else {"error": "not found"}

    def _epic_delete(epic_id):
        with SessionLocal() as s:
            return {"ok": service.delete_epic(s, epic_id)}

    def _story_get(story_id):
        with SessionLocal() as s:
            x = service.get_story(s, story_id)
            return service._ser(x) if x else {"error": "not found"}

    def _story_update(story_id, fields):
        with SessionLocal() as s:
            x = service.update_story(s, story_id, **fields)
            return service._ser(x) if x else {"error": "not found"}

    def _story_delete(story_id):
        with SessionLocal() as s:
            return {"ok": service.delete_story(s, story_id)}

    def _comment_list(task_id):
        with SessionLocal() as s:
            try:
                return [service._ser(x) for x in service.list_comments(s, task_id)]
            except service.NotFound as e:
                return {"error": str(e)}

    def _comment_create(task_id, author, content):
        with SessionLocal() as s:
            try:
                return service._ser(service.create_comment(s, task_id=task_id, author=author, content=content))
            except (service.NotFound, service.InvalidValue) as e:
                return {"error": str(e)}

    def _comment_delete(comment_id):
        with SessionLocal() as s:
            return {"ok": service.delete_comment(s, comment_id)}

    def _auth_register(username, password):
        with SessionLocal() as s:
            try:
                u = service.register_user(s, username=username, password=password)
            except service.Duplicate as e:
                return {"error": str(e)}
            return {"id": u.id, "username": u.username, "token": agent_auth.make_token(u.id)}

    def _auth_login(username, password):
        with SessionLocal() as s:
            u = service.authenticate_user(s, username=username, password=password)
            if u is None:
                return {"error": "invalid username or password"}
            return {"id": u.id, "username": u.username, "token": agent_auth.make_token(u.id)}

    def _auth_me(token):
        details = agent_auth.parse_token_details(token)
        if details is None:
            return {"error": "unauthorized"}
        with SessionLocal() as s:
            u = service.get_user(s, details[0])
            return {"id": u.id, "username": u.username} if u else {"error": "unauthorized"}

else:  # api 模式
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
        return _http("GET", "/api/projects", params={"limit": limit, "offset": offset} if limit is not None else {})

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
        return _http("GET", f"/api/projects/{project_id}/epics", params=params)

    def _epic_create(project_id, title, description):
        return _http("POST", f"/api/projects/{project_id}/epics", json={"title": title, "description": description})

    def _story_create(epic_id, title, description):
        return _http("POST", f"/api/epics/{epic_id}/stories", json={"title": title, "description": description})

    def _story_list(epic_id, limit=None, offset=0):
        params = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        return _http("GET", f"/api/epics/{epic_id}/stories", params=params)

    def _task_list(story_id, limit=None, offset=0):
        params = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        return _http("GET", f"/api/stories/{story_id}/tasks", params=params)

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
        return _http("GET", "/api/tasks", params=clean)

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
