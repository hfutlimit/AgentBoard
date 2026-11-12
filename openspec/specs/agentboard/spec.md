# AgentBoard — Capability Spec

> 当前已实现行为的唯一事实来源。变更请看 `openspec/changes/`。

## Purpose

定义 AgentBoard 已实现的项目管理能力、API、数据模型和用户界面行为，为后续变更提供可验证的当前行为基线。

## 概述
AgentBoard 是轻量项目管理工具，内嵌 OpenSpec/Superpowers 风格的规范能力：任务的 `spec` 字段存放 markdown 规范，并通过 MCP 暴露给 AI 编程工具。

## 架构（前后端分离）
三端独立，共享 `service` / `database` 层：
- **REST API**（`agentboard/api.py`，端口 8000）：纯 JSON，带 CORS。
- **Web 前端**（`agentboard/web_app.py` + `web/static/`，端口 8080）：独立 SPA，浏览器 fetch 调 API。
- **MCP 服务**（`agentboard/mcp_server.py`）：仅通过 httpx 调用 REST API；支持本地 stdio 与远程 Streamable HTTP。

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
- `POST /api/tasks/{id}/spec/append`
- `POST /api/tasks/{id}/generate-subtasks`（兼容性命名；spec 清单项 → 同一 Story 下的同级 Task，不形成 Task 嵌套）
- `GET /api/meta`（返回 types / statuses / priorities）

设置 `AGENTBOARD_REQUIRE_AUTH=1` 时，除注册、登录和 meta 外的 `/api/*` 业务端点必须携带 AgentBoard Bearer Token。Token 包含版本、用户 id、过期时间与 HMAC 签名，有效期由 `AGENTBOARD_TOKEN_TTL_SECONDS` 配置。`AGENTBOARD_ALLOW_REGISTRATION=0` 时只允许创建首个用户，之后关闭公开注册。

## MCP 工具
`list/get/create/update/delete_project, list/create/get/update/delete_epic, list/create/get/update/delete_story, list/create/get/update/delete_task, set/get/append_task_spec, set_status, search_tasks(分页/优先级), list_comments, add_comment, delete_comment, spec_proposal, generate_tasks_from_spec, auth_register, auth_login, auth_me`

> MCP 通过 `AGENTBOARD_API_URL` 调用 REST API，不直接访问数据库。列表/搜索工具支持 `limit` / `offset` 分页。`AGENTBOARD_MCP_TRANSPORT=http` 提供 `/mcp` Streamable HTTP 端点；远程端点默认验证与 REST 登录相同的 Bearer Token，并把当前请求 Token 透传给受保护 REST API。

## Requirements

### Requirement: 项目工作台 Epic 列表默认排序与状态筛选

项目工作台的 Epics 列表 SHALL 从已加载的当前项目 Epic 派生显示结果，不新增后端查询参数或服务端排序。默认筛选为“全部状态”；结果 SHALL 按 `created_at` 降序、再按 `id` 降序稳定排序，且不得原地修改已加载的原始数组。

工具栏 SHALL 提供“全部状态”以及待办、进行中、评审中、完成、已阻塞五个状态选项。选定状态时仅显示完全匹配的 Epic，切回“全部状态”时恢复所有已加载 Epic（包含不属于上述五个选项的历史状态）。筛选变更 SHALL 将分页复位至第 1 页，并保持既有进度、详情、新建、加载、错误和重试行为。

#### Scenario: 状态筛选重置分页并保留稳定排序

- **GIVEN** 当前项目已经加载多个创建时间相同或不同状态的 Epic，且用户位于非第一页
- **WHEN** 用户选择任一业务状态或切回“全部状态”
- **THEN** 列表显示对应的完整派生结果并回到第 1 页
- **AND** 结果按 `created_at DESC, id DESC` 排序，历史状态仅在“全部状态”中保持可见

### Requirement: Epic 支持已阻塞状态持久化

系统 SHALL 将 `blocked` 作为 Epic 的可持久化状态。`PATCH /api/epics/{id}` 使用
`{"status":"blocked"}` 更新既有 Epic 时 SHALL 返回 HTTP 200，并在响应与后续读取中
返回 `status: "blocked"`。SQLite 与 MariaDB 的 `ck_epics_status` 约束 SHALL 与 ORM
模型一致，允许 `backlog`、`todo`、`in_progress`、`in_review`、`verifying`、`done` 和
`blocked`，不得将该合法请求泄漏为数据库完整性错误或 HTTP 500。

#### Scenario: 通过 API 将 Epic 标记为已阻塞

- **GIVEN** 一个已持久化的 Epic
- **WHEN** 客户端调用 `PATCH /api/epics/{id}` 并提交 `{"status":"blocked"}`
- **THEN** 服务返回 HTTP 200 和 `status: "blocked"`
- **AND** 后续读取该 Epic 时状态仍为 `blocked`

### Requirement: Server 拥有工作规划状态机

Server SHALL 从 Task/Proposal 业务状态现算每个已映射项目的下一单位工作
（类型/状态 → work kind、readiness 与人审门、讨论轮次奇偶归属、Proposal 轮次），
经 `work_key` 幂等物化 `worker_work` 行，并收尾全部 Task 已 done 的 Story。
规划 SHALL 在 outbox relay 之前运行，默认 5s 节流（`AGENTBOARD_WORKER_PLAN_SECONDS`
可调），失败只记日志不阻塞 relay。规划 SHALL NOT 选择具体 Agent——工作由共享队列
竞争消费。Worker SHALL 只做 presence 心跳、项目范围声明与消费执行。

#### Scenario: 重复规划不重复派活

- **GIVEN** 一个已映射项目存在可工作的 Task，planner 已物化一行 `worker_work`
- **WHEN** 业务状态未变，再跑一轮规划
- **THEN** 新建行计数为 0，队列中仍只有一行同 `work_key` 的工作

### Requirement: Worker 项目范围声明按成员资格收敛

`POST /api/worker-work/projects` SHALL 对列出的每个项目校验调用者的项目成员
资格后写入 `worker_project_mappings`（重复声明幂等，重新列出会重新启用）。
不再列出的映射 SHALL 被置 `enabled=False`，但禁用每个受影响项目前 SHALL 校验
调用者在该项目的成员资格——Worker 跨用户共享、无 owner，任何成员不得借自报
`worker_id` 缩减别的项目的工作范围；非成员项目的映射留给项目管理员经
`/api/projects/{pid}/workers` 管理。`project_ids` SHALL 允许空集，用于退掉
调用者有权管理的全部范围。

#### Scenario: 成员不能缩减别的项目的 Worker 范围

- **GIVEN** Worker W 同时映射项目 A（用户甲成员）与项目 B（用户乙成员）
- **WHEN** 乙调用该端点声明 W 只负责 B（或声明空集）
- **THEN** B 的映射按声明更新，A 的映射保持 `enabled=True`

### Requirement: 能力覆盖未知即 fail-open

规划 SHALL 用已映射 Worker 的在线 AgentInstance 能力与七种 work kind 的交集
决定是否建行：覆盖未知（无映射或能力数据不可读，含 malformed JSON）SHALL 不过滤；
有映射但无在线实例 SHALL 不建行。`AgentInstance.agent_id` SHALL 按逻辑串
（`Agent.agent_id`）关联，不得混用整数主键。

#### Scenario: 脏能力数据不停摆规划

- **GIVEN** 某在线 Agent 的 capabilities 是非法 JSON
- **WHEN** planner 计算该项目覆盖
- **THEN** 覆盖视为未知，规划照常建行，不抛异常

### Requirement: 被丢弃消息的可用工作 SHALL 被重发

`available` 且已发布（`published_at` 非空）超过 3 分钟 grace 的行，只要工作仍可
offer 且覆盖恢复，planner SHALL 重置其 `published_at` 让 relay 重发同一行
（同 `work_key`，不建新行）。`leased` 行 SHALL NOT 被 planner 触碰（归 relay
的租约过期路径）。该不变量覆盖 ack-and-drop、ack 后崩溃等消息消失场景。

#### Scenario: ack-drop 的工作在 Agent 恢复后继续

- **GIVEN** 一行已发布的可用工作，其消息被无合格 Agent 的 Worker ack-drop
- **WHEN** 能 serve 该 kind 的 Agent 重新在线，grace 已过，planner 再跑一轮
- **THEN** 该行 `published_at` 被重置，relay 重新发布，不产生重复行

### Requirement: serverplan 协议门禁与滚动升级

Server SHALL 在 `GET /api/worker-work/snapshot` 返回协议串
`worker-work.serverplan.v1`；新 Worker 读不到该值 SHALL 拒绝启动。
`POST /offers` 与 `POST /stories/{id}/complete` SHALL 保留，并与 planner 共享
同一套 `materialize_offer` 幂等物化，混合集群不重复派活。

#### Scenario: 旧 Worker 与新 Server 混合不重复派活

- **GIVEN** 新 Server 的 planner 已物化某工作行
- **WHEN** 旧 Worker 扫描同一业务对象并 POST `/offers`
- **THEN** 命中同一 `work_key`，返回既有行，不新建

### Requirement: Server 补齐默认工作计划

Proposal converge 且允许自动建单但未给 `ticket_plan` 时，Server SHALL 补齐
规范 design → dev → QA 三任务 DAG。QA 缺陷 confirm 未给 `qa_followup` 时，
Server SHALL 从不可变 QA 证据构造 bug + 独立复测计划。Worker 显式传入的计划
SHALL 继续接受，但必须与不可变证据逐字匹配，否则 422。

#### Scenario: confirm 不带 followup 由 Server 建 bug 与复测

- **GIVEN** 一轮失败 QA 的证据含已确认缺陷，讨论进入 confirm
- **WHEN** Worker 提交 confirm 但不带 `qa_followup`
- **THEN** Server 创建缺陷 bug Task 与依赖其的复测 QA Task，原 QA 证据不变

## Web UI
项目树浏览；Project/Epic/Story/Task/Bug 全量增删改；状态流转；任务优先级徽章与编辑；description/spec 编辑；评论时间线；Story 列表/看板的任务详情抽屉；「插入 OpenSpec 提案模板」「从 spec 生成同级任务」按钮；markdown 渲染。

## 规范约定
- Task.spec 存放 OpenSpec/Superpowers markdown；`spec_proposal` 生成标准提案结构：背景 / 目标 / 范围 / 任务清单 / 验收标准。
- `generate_tasks_from_spec` 解析 spec 中 `- [ ] 标题` 清单项，在同一 Story 下生成同级 Task；生成项以 `source_spec_id` 反向关联源任务，并在源 spec 末尾回写链接（双向引用）。“subtask”仅是兼容性接口名称，不表示数据层级嵌套。
- 存储：调试 SQLite，生产 MariaDB，经 `AGENTBOARD_DB_URL` 切换，代码不感知具体数据库。
