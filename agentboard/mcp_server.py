"""AgentBoard MCP 服务：暴露项目管理与 spec 读写工具给 AI Agent。

运行：python -m agentboard.mcp_server   （默认 stdio 传输）
"""
from fastmcp import FastMCP
from .database import SessionLocal
from . import service
from .service import NotFound, IllegalTransition
from .models import ItemType, Status

mcp = FastMCP("AgentBoard")


def _ok(obj):
    if obj is None:
        return {"ok": False, "error": "not found"}
    return {"ok": True, **service._ser(obj)}


def _list(rows):
    return [service._ser(r) for r in rows]


@mcp.tool()
def create_project(name: str, key: str | None = None, description: str = "") -> dict:
    """创建项目。name 必填，key 为短码(可选)，description 为 markdown。"""
    with SessionLocal() as s:
        return _ok(service.create_project(s, name=name, key=key, description=description))


@mcp.tool()
def list_projects() -> list:
    """列出所有项目。"""
    with SessionLocal() as s:
        return _list(service.list_projects(s))


@mcp.tool()
def create_epic(project_id: int, title: str, description: str = "") -> dict:
    """在指定项目下创建 Epic。"""
    with SessionLocal() as s:
        return _ok(service.create_epic(s, project_id=project_id, title=title, description=description))


@mcp.tool()
def create_story(epic_id: int, title: str, description: str = "") -> dict:
    """在指定 Epic 下创建 Story。"""
    with SessionLocal() as s:
        return _ok(service.create_story(s, epic_id=epic_id, title=title, description=description))


@mcp.tool()
def create_task(project_id: int, story_id: int | None, title: str,
                type: str = "task", description: str = "", spec: str = "") -> dict:
    """在指定 Story 下创建 Task/Bug。type 为 task 或 bug。"""
    with SessionLocal() as s:
        return _ok(service.create_task(s, project_id=project_id, story_id=story_id,
                                       title=title, type=type, description=description, spec=spec))


@mcp.tool()
def get_task(task_id: int) -> dict:
    """获取任务详情。"""
    with SessionLocal() as s:
        return _ok(service.get_task(s, task_id))


@mcp.tool()
def update_task(task_id: int, title: str | None = None, description: str | None = None,
                spec: str | None = None, type: str | None = None) -> dict:
    """更新任务的标题/描述/spec/类型。"""
    fields = {k: v for k, v in dict(title=title, description=description, spec=spec, type=type).items() if v is not None}
    with SessionLocal() as s:
        return _ok(service.update_task(s, task_id, **fields))


@mcp.tool()
def set_task_spec(task_id: int, spec: str) -> dict:
    """设置任务的 spec（OpenSpec/Superpowers 风格 markdown）。"""
    with SessionLocal() as s:
        return _ok(service.set_task_spec(s, task_id, spec))


@mcp.tool()
def append_task_spec(task_id: int, text: str) -> dict:
    """向任务 spec 追加内容。"""
    with SessionLocal() as s:
        return _ok(service.append_task_spec(s, task_id, text))


@mcp.tool()
def get_task_spec(task_id: int) -> dict:
    """读取任务 spec 原文。"""
    with SessionLocal() as s:
        t = service.get_task(s, task_id)
        return {"ok": True, "task_id": task_id, "spec": t.spec} if t else {"ok": False}


@mcp.tool()
def set_status(task_id: int, status: str) -> dict:
    """变更任务状态。合法迁移见文档 FR-5。"""
    with SessionLocal() as s:
        try:
            return _ok(service.set_status(s, task_id, status))
        except (NotFound, IllegalTransition) as e:
            return {"ok": False, "error": str(e)}


@mcp.tool()
def search_tasks(project_id: int | None = None, epic_id: int | None = None,
                 story_id: int | None = None, type: str | None = None,
                 status: str | None = None, q: str | None = None) -> list:
    """按条件搜索任务，q 为关键字(匹配 title/description/spec)。"""
    with SessionLocal() as s:
        return _list(service.search_tasks(s, project_id=project_id, epic_id=epic_id,
                                          story_id=story_id, type=type, status=status, q=q))


@mcp.tool()
def spec_proposal(task_id: int, title: str, background: str, goal: str,
                  scope: str, tasks: str, acceptance: str) -> dict:
    """生成 OpenSpec 风格变更提案并写入任务 spec。

    tasks / acceptance 可用换行分隔的多行文本。
    """
    md = (
        f"# 变更提案：{title}\n\n"
        f"## 背景\n{background}\n\n"
        f"## 目标\n{goal}\n\n"
        f"## 范围\n{scope}\n\n"
        f"## 任务清单\n{tasks}\n\n"
        f"## 验收标准\n{acceptance}\n"
    )
    with SessionLocal() as s:
        return _ok(service.set_task_spec(s, task_id, md))


if __name__ == "__main__":
    mcp.run()
