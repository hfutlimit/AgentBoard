"""AgentBoard MCP 服务（独立）。

后端可切换（前后端分离友好）：
- AGENTBOARD_MCP_BACKEND=api （默认）：通过 httpx 调用 REST API，地址 AGENTBOARD_API_URL（默认 http://127.0.0.1:8000）
- AGENTBOARD_MCP_BACKEND=db        ：直连数据库（复用 service 层，无需启动 API）

运行：python -m agentboard.mcp_server   （stdio 传输）
"""
import os
from fastmcp import FastMCP

BACKEND = os.getenv("AGENTBOARD_MCP_BACKEND", "api").lower()
API_URL = os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000")

mcp = FastMCP("AgentBoard")


# ===================== 后端实现 =====================
if BACKEND == "db":
    from .database import SessionLocal
    from . import service

    def _proj_list():
        with SessionLocal() as s:
            return [service._ser(p) for p in service.list_projects(s)]

    def _proj_create(name, key, description):
        with SessionLocal() as s:
            return service._ser(service.create_project(s, name=name, key=key, description=description))

    def _epic_create(project_id, title, description):
        with SessionLocal() as s:
            return service._ser(service.create_epic(s, project_id=project_id, title=title, description=description))

    def _story_create(epic_id, title, description):
        with SessionLocal() as s:
            return service._ser(service.create_story(s, epic_id=epic_id, title=title, description=description))

    def _task_create(project_id, story_id, title, type, description, spec):
        with SessionLocal() as s:
            return service._ser(service.create_task(s, project_id=project_id, story_id=story_id,
                                                    title=title, type=type, description=description, spec=spec))

    def _task_get(task_id):
        with SessionLocal() as s:
            t = service.get_task(s, task_id)
            return service._ser(t) if t else {"error": "not found"}

    def _task_update(task_id, fields):
        with SessionLocal() as s:
            t = service.update_task(s, task_id, **fields)
            return service._ser(t) if t else {"error": "not found"}

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

else:  # api 模式
    import httpx

    def _http(method, path, **kw):
        with httpx.Client(base_url=API_URL, timeout=15) as c:
            r = c.request(method, path, **kw)
            if r.status_code >= 400:
                try:
                    return {"error": r.json().get("detail", r.text)}
                except Exception:
                    return {"error": r.text}
            return r.json() if r.content else {"ok": True}

    def _proj_list():
        return _http("GET", "/api/projects")

    def _proj_create(name, key, description):
        return _http("POST", "/api/projects", json={"name": name, "key": key, "description": description})

    def _epic_create(project_id, title, description):
        return _http("POST", f"/api/projects/{project_id}/epics", json={"title": title, "description": description})

    def _story_create(epic_id, title, description):
        return _http("POST", f"/api/epics/{epic_id}/stories", json={"title": title, "description": description})

    def _task_create(project_id, story_id, title, type, description, spec):
        return _http("POST", f"/api/stories/{story_id}/tasks",
                     json={"project_id": project_id, "title": title, "type": type,
                           "description": description, "spec": spec})

    def _task_get(task_id):
        return _http("GET", f"/api/tasks/{task_id}")

    def _task_update(task_id, fields):
        return _http("PATCH", f"/api/tasks/{task_id}", json=fields)

    def _task_status(task_id, status):
        return _http("PUT", f"/api/tasks/{task_id}/status", json={"status": status})

    def _task_search(params):
        clean = {k: v for k, v in params.items() if v is not None}
        return _http("GET", "/api/tasks", params=clean)

    def _task_generated(task_id):
        return _http("POST", f"/api/tasks/{task_id}/generate-subtasks")


# ===================== MCP 工具 =====================
@mcp.tool()
def list_projects() -> list:
    """列出所有项目。"""
    return _proj_list()


@mcp.tool()
def create_project(name: str, key: str | None = None, description: str = "") -> dict:
    """创建项目。name 必填，key 为短码，description 为 markdown。"""
    return _proj_create(name, key, description)


@mcp.tool()
def create_epic(project_id: int, title: str, description: str = "") -> dict:
    """在指定项目下创建 Epic。"""
    return _epic_create(project_id, title, description)


@mcp.tool()
def create_story(epic_id: int, title: str, description: str = "") -> dict:
    """在指定 Epic 下创建 Story。"""
    return _story_create(epic_id, title, description)


@mcp.tool()
def create_task(project_id: int, story_id: int, title: str,
                type: str = "task", description: str = "", spec: str = "") -> dict:
    """在指定 Story 下创建 Task/Bug。type 为 task 或 bug。"""
    return _task_create(project_id, story_id, title, type, description, spec)


@mcp.tool()
def get_task(task_id: int) -> dict:
    """获取任务详情（含 description 与 spec）。"""
    return _task_get(task_id)


@mcp.tool()
def update_task(task_id: int, title: str | None = None, description: str | None = None,
                spec: str | None = None, type: str | None = None) -> dict:
    """更新任务标题/描述/spec/类型。"""
    fields = {k: v for k, v in dict(title=title, description=description, spec=spec, type=type).items() if v is not None}
    return _task_update(task_id, fields)


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
def set_status(task_id: int, status: str) -> dict:
    """变更任务状态（校验合法迁移，见文档 FR-5）。"""
    return _task_status(task_id, status)


@mcp.tool()
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, type: str | None = None,
                 status: str | None = None, q: str | None = None) -> list:
    """按条件搜索任务，q 为关键字(匹配 title/description/spec)。"""
    return _task_search(dict(project_id=project_id, epic_id=epic_id, story_id=story_id,
                             type=type, status=status, q=q))


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


if __name__ == "__main__":
    mcp.run()
