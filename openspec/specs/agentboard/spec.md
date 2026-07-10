# AgentBoard — Capability Spec

> 当前已实现行为的唯一事实来源。变更请看 `openspec/changes/`。

## 概述
AgentBoard 是轻量项目管理工具，内嵌 OpenSpec/Superpowers 风格的规范能力：任务的 `spec` 字段存放 markdown 规范，并通过 MCP 暴露给 AI 编程工具。

## 架构（前后端分离）
三端独立，共享 `service` / `database` 层：
- **REST API**（`agentboard/api.py`，端口 8000）：纯 JSON，带 CORS。
- **Web 前端**（`agentboard/web_app.py` + `web/static/`，端口 8080）：独立 SPA，浏览器 fetch 调 API。
- **MCP 服务**（`agentboard/mcp_server.py`）：默认 httpx 调 API，可切换 `AGENTBOARD_MCP_BACKEND=db` 直连数据库。

## 数据模型
`Project → Epic → Story → Task/Bug`，**Task 为最底层、不嵌套**。
字段：`id, project_id, story_id, type(task|bug), title, status, description(md), spec(md), source_spec_id, created_at, updated_at`。
状态：`backlog → todo → in_progress → in_review / verifying → done`，含合法迁移校验。

## REST API
- `GET/POST /api/projects`、`GET/PATCH/DELETE /api/projects/{id}`
- `GET/POST /api/projects/{id}/epics`、`GET/PATCH/DELETE /api/epics/{id}`
- `GET/POST /api/epics/{id}/stories`、`GET/PATCH/DELETE /api/stories/{id}`
- `GET/POST /api/stories/{id}/tasks`、`GET/PATCH/DELETE /api/tasks/{id}`
- `PUT /api/tasks/{id}/status`、`GET /api/tasks`（搜索：project/epic/story/type/status/q）
- `POST /api/tasks/{id}/generate-subtasks`（spec 清单项 → 同级子任务）
- `GET /api/meta`（返回 types / statuses）

## MCP 工具
`list_projects, create_project, create_epic, create_story, create_task, get_task, update_task, set_task_spec, get_task_spec, set_status, search_tasks, spec_proposal, generate_tasks_from_spec`

## Web UI
项目树浏览；Project/Epic/Story/Task/Bug 全量增删改；状态流转；description/spec 编辑；「插入 OpenSpec 提案模板」「从 spec 生成子任务」按钮；markdown 渲染。

## 规范约定
- Task.spec 存放 OpenSpec/Superpowers markdown；`spec_proposal` 生成标准提案结构：背景 / 目标 / 范围 / 任务清单 / 验收标准。
- `generate_tasks_from_spec` 解析 spec 中 `- [ ] 标题` 清单项，生成同级子任务，子任务以 `source_spec_id` 反向关联源任务，并在源 spec 末尾回写链接（双向引用）。
- 存储：调试 SQLite，生产 MariaDB，经 `AGENTBOARD_DB_URL` 切换，代码不感知具体数据库。
