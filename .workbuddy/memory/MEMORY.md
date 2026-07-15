# AgentBoard 项目长期记忆

## 项目定位
轻量项目管理工具 + OpenSpec/Superpowers 风格"规范驱动开发"；任务（task）挂载 `description` 与 `spec`（markdown），经 MCP 暴露给 AI Agent。层级：Project → Epic → Story → Task/Bug。存储双后端（SQLite 调试 / MariaDB 生产）。

## 前端优化长期轨道（重要约定）
- 来源：用户要求"持续优化前端、模仿 Jira、每次只做小的优化"作为长期任务。
- 权威工作清单：`docs/tasks.md` → **Epic 11**；process 容器：`openspec/changes/frontend-continuous/`。
- **迭代纪律**：每个自动任务周期只交付**一项**小优化；单文件改动为主、新增前端代码 < ~80 行、不引入新框架/打包依赖、**不改 `models.py`/`api.py` 契约**。
- **Backlog A（纯前端，可直接做）**：看板只读视图、状态色徽章、类型图标、行内编辑、全局搜索、状态按钮组、骨架屏、空状态、进度条、深色模式、响应式、Toast 动画、详情抽屉、MD 工具栏、快捷键、复制链接、路由动画、面包屑高亮、hover 操作、前端偏好存储。
- **Backlog B（需后端，单独提 change 评估）**：labels、assignee、due_date、看板拖拽排序、评论/活动流、列表分组排序。
- commit 规范：`feat(ui): 前端小优化 - <一句话描述>`。
- 复用：状态/类型枚举来自 `GET /api/meta`；渲染辅助 `md()/esc()/statusSelect()/typeSelect()/toast()/route()` 可直接复用。

## 既有能力现状（2026-07-15 22:00 更新）
- **Epic 15 (用户体验持续优化 v0.4+) 已完成**（id=89）✅
  - Story 15.1 全局通知与操作反馈：每条通知项加类型图标（5 种类型各对应主题色）+ 错落入场动画
  - Story 15.2 最近访问与收藏：修复 loadRecentProjects 刷新 bug + 新增收藏功能（侧边栏分组、星标按钮）
- **Epic 22/23/24 已完成**：审计日志、任务依赖、Webhook、缓存强化、Toast、移动端优化全部 done
- **Epic 21 Story 15/16 已完成**：API 指数退避重试（429/5xx，1s/2s/4s）+ 离线队列（localStorage）+ 批量操作确认 → done ✅
- **Epic 25 (Epic 8 in DB) 进行中**：Stories 25.1-25.4，Tasks 600-605 → in_review
  - Task 600: 看板卡片优先级色边框
  - Task 601: 看板卡片完成进度显示
  - Task 602: 任务列表高级筛选面板
  - Task 603: 抽屉内快速操作按钮
- **前端指数退避重试**：`api.service.ts` 的 `request()` 方法带 `_retries` 参数，retryable 错误自动退避重试
- **离线队列**：写入操作离线时入 localStorage（最多 50 条），网络恢复时 toast 提示
- **Docker Web**：volume mount `E:/Projects/WorkBuddy/AgentBoard/agentboard/web/static -> /app/...`，无需 rebuild
- **API 端口**：58125（非 8000），验证用 `curl http://localhost:58125/api/meta`
- **测试约定**：module-scoped fixture 启动独立 server；backend_flow 单独运行通过，组合运行时 fixture 冲突
- **Sprint 功能就绪（2026-07-12）**：`SprintStatus` 枚举（planning/active/completed）+ `Sprint` 模型 + Task.sprint_id FK；单 active Sprint 约束；Sprint 完成时未完成任务退回 backlog；REST API Sprint 端点完整；`/api/meta` 含 `sprint_statuses`。

## 协作流程约定
- 文档驱动：需求 `docs/requirements.md`、主任务 `docs/tasks.md`（Epic 分段）、每个变更 `openspec/changes/<id>/{proposal,design,tasks}.md`。
- Git（⚠️ 本条为本项目硬性约定）：**每次修改都要及时 push**。无论改动大小（含文档/任务拆分/数据库运行时以外的任何文件变更），完成 `git add . && git commit -m "feat: ..."` 后**必须立即 `git push origin main`**。push 若失败（沙箱 SSH 受限）需提示用户本地重试，不得静默跳过。

## 前端开发规范（强制流程，2026-07-12）
- **修改前端代码 → 部署 → Playwright 验证**：每次修改前端代码后，完成部署必须用 Playwright 截图验证页面渲染正常、无 JS 错误、无 404 资源。
- Playwright 验证检查项：Page Errors、404 Resources、Angular 组件是否渲染。
- 参考验证脚本模式：启动 browser、访问 http://localhost:8080/、截图、控制台错误检查。

## 部署约定（重要，踩坑记录 2026-07-12）
- **前端改动必须重建镜像**：`web_app.py` 从 `agentboard/web/static/` 读文件，但 Dockerfile 用 `COPY . .` 把源码**构建时**烤进镜像；`docker-compose.yml` 的 web/api 服务**只挂了 `agentboard_data` 数据卷，没挂源码**。因此改了静态文件后，只跑 `docker compose up -d` 会复用旧镜像 → 看到老页面。
- **正确重部署命令**：`docker compose up -d --build`（或先 `docker compose build` 再 `up -d`）。本次已在沙箱执行 `docker compose up -d --build web` 修复"看不到新前端"问题。
- Web 端口 **8080**（非 5080），API 端口 8000。浏览器访问 http://localhost:8080 ，SPA 经 `AGENTBOARD_API_URL`（默认 localhost:8000）调 API。
- **浏览器务必硬刷新**（Ctrl/Cmd+Shift+R）清静态缓存。
- **后端改动 docker cp 注入**：沙箱 Docker Hub 不可达，`docker compose build` 失败；改用 `docker cp <file> agentboard-api-1:/app/<path>` 注入修改文件；`docker restart agentboard-api-1` 重启；注意：`docker cp` 复制目录到已存在的目标时会**嵌套复制**（创建 `dest/src/` 而非覆盖内容），需用 `docker exec rm -rf` 清理嵌套目录。
- **Alembic 迁移与 init_db**：API 启动时 `init_db()` 调用 `alembic.command.upgrade("head")`；新迁移必须同时复制到容器 `/app/migrations/versions/` 并更新 `alembic_version` 表；沙箱无 mysql 客户端时用容器内 Python 直接 SQL 应用。
- **前端改动必须重建镜像**：`web_app.py` 从 `agentboard/web/static/` 读文件，但 Dockerfile 用 `COPY . .` 把源码**构建时**烤进镜像；`docker-compose.yml` 的 web/api 服务**只挂了 `agentboard_data` 数据卷，没挂源码**。因此改了静态文件后，只跑 `docker compose up -d` 会复用旧镜像 → 看到老页面。
- **正确重部署命令**：`docker compose up -d --build`（或先 `docker compose build` 再 `up -d`）。本次已在沙箱执行 `docker compose up -d --build web` 修复"看不到新前端"问题。
- Web 端口 **8080**（非 5080），API 端口 8000。浏览器访问 http://localhost:8080 ，SPA 经 `AGENTBOARD_API_URL`（默认 localhost:8000）调 API。
- 部署后浏览器务必**硬刷新**（Ctrl/Cmd+Shift+R）清静态缓存。

## CORS 踩坑记录（2026-07-12）
- **问题**：`require_business_auth` 中间件返回 401 `JSONResponse` 时，CORS 中间件未能注入 `Access-Control-Allow-Origin` 头 → 浏览器报 CORS 错误，SPA 无法读取 401 响应。
- **根因**：Starlette `CORSMiddleware` 对直接返回的 `JSONResponse`（非经 `call_next` 的响应）可能不注入 CORS 头。
- **修复**：在 `require_business_auth` 中手动为 401 响应添加 `Access-Control-Allow-Origin`（取自请求 `Origin` 头）和 `Access-Control-Allow-Credentials: true`。
- **文件**：`agentboard/api.py` L49-55。

## API Schema 关键约定（2026-07-12 盘点）
- **SprintIn**: `title`（非 `name`）、`goal`、`start_date`、`end_date`。无 `status` 字段（默认 planning）。
- **SprintPatch**: 同 SprintIn 但全部可选。更新用 **PATCH**（非 PUT）。
- **TaskIn**: 必须含 `project_id`；无 `status`（默认 backlog）和 `sprint_id`（默认 null）字段。
- **TaskPatch**: 含 `sprint_id`（可 null）。改状态用 `PUT /api/tasks/{tid}/status`（非 PATCH）。
- **任务状态机**: backlog → todo → in_progress（不允许 backlog → in_progress 直接跳转）→ in_review → verifying → done。

## Sprint 功能就绪状态（2026-07-12 14:10 更新）
- Task #82 (Sprint API) → **done** ✅ (46/46 tests passed)
- Task #83 (Sprint UI) → **done** ✅ (13/13 tests passed)
- Sprint CRUD + activate + complete + 单 active 约束 + 任务退回 全部正常。

## 新增数据模型（2026-07-12 21:10）
- **Project**: `is_private` 字段（私有项目仅成员可见）
- **User**: `is_admin` 字段（全局管理员）
- **ProjectMember**: `project_id`/`user_id`/`role`(owner|member)/`joined_at`
- **Notification**: `user_id`/`type`(project_invite|join_request|task_assigned|status_changed|mentioned)/`title`/`content`/`is_read`/`link`/`created_at`

## 新增 API 端点（2026-07-12）
- GET/POST `/api/projects/{pid}/members` — 成员管理
- DELETE/PATCH `/api/projects/{pid}/members/{uid}` — 移除/变更角色
- GET `/api/notifications` — 通知列表（支持 unread_only）
- GET `/api/notifications/unread-count` — 未读计数
- POST `/api/notifications/{nid}/read` — 标记已读
- POST `/api/notifications/read-all` — 全部已读
- DELETE `/api/notifications/{nid}` — 删除通知
- GET `/api/projects/{pid}/stats` — 项目统计数据
- GET/PATCH `/api/admin/users` — 管理员用户管理
- GET/DELETE `/api/admin/projects` — 管理员项目管理

## 项目成员权限规则（2026-07-12）
- 项目创建者自动成为 Owner
- Owner 可邀请/移除成员、变更角色、编辑项目设置
- Admin 拥有全局 Owner 权限
- Private 项目：非成员无法访问；Public 项目：所有人可见

## API Key 管理（Epic 25，2026-07-14 实现完整）

### 数据模型
- 表 `api_keys` 已迁移（alembic `c4e8a1b2d3f4`）：id, user_id, name, key_prefix, key_hash, permissions(JSON str), enabled, created_at, updated_at, last_used_at
- 密钥生成：`auth.generate_api_key()` → `abk_<secrets.token_urlsafe(32)>`，仅存 SHA256 hash

### API 端点（全部要求登录 Bearer Token）
- `POST /api/api-keys` body={name, permissions[]} → 返回完整 key（仅此一次）+ 明文
- `GET /api/api-keys` → 列表（不返回明文）
- `GET /api/api-keys/{id}` → 详情
- `PATCH /api/api-keys/{id}` → 更新 name/enabled/permissions
- `DELETE /api/api-keys/{id}` → 撤销

### 中间件认证
- `require_business_auth` 中兼容 `Bearer abk_xxx` 头：sha256 查表 → 注入 user_id → 更新 last_used_at
- 要求 `AGENTBOARD_REQUIRE_AUTH=1` 才生效（默认 0，仅登录/注册/meta/health 公开）

### 任务状态机（重要！2026-07-14 踩坑）
- `TRANSITIONS`: backlog→todo; todo↔in_progress; in_progress→in_review/verifying/todo; in_review→done/in_progress; verifying→done/in_progress
- **in_review → verifying 非法**！必须从 in_review 退回 in_progress 才能到 verifying
- 状态转移用 `PUT /api/tasks/{tid}/status` body={status}（不是 PATCH /api/tasks/{tid}）

## 用户/项目 owner 配置（2026-07-14）
- 提权：直接 SQLite 改 `users.is_admin=1`（容器内 `/app/data/agentboard.db`，不是 MariaDB！）
- 加 owner：`INSERT INTO project_members (project_id, user_id, role, joined_at) VALUES (?,?, 'owner', datetime('now'))`
- 当前账号 jzhong2026 (id=3) 已是 admin + 4 项目 owner（projects 1,2,3,4）

## 自动化任务关键经验（2026-07-15）
- **MCP 状态更新绕过**：沙箱中 `mcp__agentboard__set_status` 持续报 "task_id must be integer"（DeferExecuteTool 序列化 bug），用 curl 直接调 REST：
  - `PUT /api/tasks/{tid}/status` body=`{"status":"..."}` — 任务状态变更
  - `PATCH /api/stories/{sid}` body=`{"status":"..."}` — Story 状态
  - `PATCH /api/epics/{eid}` body=`{"status":"..."}` — Epic 状态
  - 任务状态机：`backlog → todo → in_progress → in_review → done`（不可跳跃）
- **容器 api.py 与本地可能不同步**：容器内 `/app/agentboard/api.py` 可能是数小时前的版本（mtime 15:09 vs 本地 23:09），导致 API 路由 404 但代码存在。`docker exec grep` 验证容器端代码，必要时 `docker cp` 注入。
- **Playwright 拦截 API 模拟数据**：用 `context.route("**/api/notifications**", mock_route)` 拦截 Angular HttpClient（XHR）调用，绕过 404 返回 mock 数据用于 UI 验证。
- **Web 容器 volume mount**：web 服务挂载 `./agentboard/web/static:/app/agentboard/web/static:ro`，前端改文件 → `cp` 到 static 即可，无需 rebuild。`index.html` 引用的 hashed JS 名要同步更新（每次 ng build 都会变）。
