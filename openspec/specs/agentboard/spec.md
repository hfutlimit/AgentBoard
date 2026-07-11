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
字段：`id, project_id, story_id, type(task|bug), title, status, priority, description(md), spec(md), source_spec_id, created_at, updated_at`。`priority` 为 `highest|high|medium|low|lowest`，默认 `medium`。
状态：`backlog → todo → in_progress → in_review / verifying → done`，含合法迁移校验。

Task 拥有按创建时间正序排列的评论流；评论字段为 `id, task_id, author, content(md), created_at, updated_at`。删除任务或其父级时评论同步清理。

## REST API
- `GET/POST /api/projects`、`GET/PATCH/DELETE /api/projects/{id}`
- `GET/POST /api/projects/{id}/epics`、`GET/PATCH/DELETE /api/epics/{id}`
- `GET/POST /api/epics/{id}/stories`、`GET/PATCH/DELETE /api/stories/{id}`
- `GET/POST /api/stories/{id}/tasks`、`GET/PATCH/DELETE /api/tasks/{id}`
- `PUT /api/tasks/{id}/status`、`GET /api/tasks`（搜索：project/epic/story/type/status/priority/q）
- `GET/POST /api/tasks/{id}/comments`、`DELETE /api/comments/{id}`
- `POST /api/tasks/{id}/generate-subtasks`（兼容性命名；spec 清单项 → 同一 Story 下的同级 Task，不形成 Task 嵌套）
- `GET /api/meta`（返回 types / statuses / priorities）

## MCP 工具
`list_projects(分页), create_project, create_epic, create_story, create_task, get_task, update_task, set_task_spec, get_task_spec, set_status, search_tasks(分页/优先级), list_comments, add_comment, delete_comment, spec_proposal, generate_tasks_from_spec, get_epic, update_epic, delete_epic, get_story, update_story, delete_story`

> MCP 默认 `AGENTBOARD_MCP_BACKEND=api` 调 REST；可设 `db` 直连。列表/搜索工具支持 `limit` / `offset` 分页。

## Web UI
项目树浏览；Project/Epic/Story/Task/Bug 全量增删改；状态流转；任务优先级徽章与编辑；description/spec 编辑；评论时间线；Story 列表/看板的任务详情抽屉；「插入 OpenSpec 提案模板」「从 spec 生成同级任务」按钮；markdown 渲染。

## 规范约定
- Task.spec 存放 OpenSpec/Superpowers markdown；`spec_proposal` 生成标准提案结构：背景 / 目标 / 范围 / 任务清单 / 验收标准。
- `generate_tasks_from_spec` 解析 spec 中 `- [ ] 标题` 清单项，在同一 Story 下生成同级 Task；生成项以 `source_spec_id` 反向关联源任务，并在源 spec 末尾回写链接（双向引用）。“subtask”仅是兼容性接口名称，不表示数据层级嵌套。
- 存储：调试 SQLite，生产 MariaDB，经 `AGENTBOARD_DB_URL` 切换，代码不感知具体数据库。
