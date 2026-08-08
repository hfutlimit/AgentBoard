# AgentBoard 项目长期记忆

## 定位与架构
轻量项目管理 + OpenSpec。层级 Project→Epic→Story→Task/Bug。双后端 SQLite 调试 / MariaDB 生产（`AGENTBOARD_DB_URL`）。前端单体 `App` 组件（`frontend/src/app/app.{ts,html,css}`），standalone 无 NgModule，假路由（`view()` signal + `@switch`）。UI 弹窗统一走 `modal()`/`docModal`/`sprintModalOpen`，样式 `.modal-overlay`。**统一 markdown 管线**：`renderMarkdown()` 是文档/任务/Story/Epic 描述与全部评论区的唯一入口（Epic 64 S2/S3/S4 图片支持 + XSS 白名单即在此一处实现，CSS 3616+ 全容器类含暗色主题）。

## API / 状态机（2026-08-08 全量核对源码版）
- **仅 Task/Bug 有强制迁移表**（service.py `TRANSITIONS` + `set_status` 校验，BFS 求最短路径勿硬编码）。完整表：`BACKLOG→TODO/BLOCKED`；`TODO→IN_PROGRESS/BACKLOG/DONE/BLOCKED`；`IN_PROGRESS→IN_REVIEW/VERIFYING/TODO/DONE/BLOCKED`；`IN_REVIEW→DONE/IN_PROGRESS/BLOCKED/FINAL_REVIEW`；`FINAL_REVIEW→DONE/IN_REVIEW/BLOCKED`；`VERIFYING→DONE/IN_PROGRESS/BLOCKED`；`DONE→IN_PROGRESS/TODO/BLOCKED`；`BLOCKED→TODO/IN_PROGRESS`。**无 in_review→verifying**。A-22 例外：TODO/IN_PROGRESS→DONE、DONE→TODO。**Bug 与 Task 同表同状态机，仅 `type` 字段区分**（tasks 表 ck_tasks_status 11 值）。
- **Epic 123 设计评审流**：`transitions_for(needs_design)` 动态迁移表（service.py）。Story.needs_design（默认 true）→ TODO 必须先进 `IN_DESIGN→DESIGN_PENDING_REVIEW→DESIGN_REVIEW_APPROVED`（禁直跳 IN_PROGRESS）；false → TODO→IN_PROGRESS 快速流；后段 in_review→final_review→done 共用。**blocked 全向可达**（任意态→BLOCKED，含 done）；进入 blocked 记 `tasks.previous_status`，解除恢复之；`set_status`/batch/claim/review/多数决/超时全路径写 `task_status_history`（_record_status_history；GET /api/tasks/{tid}/status-history）。`StatusIn.reason` 可选。⚠️ **状态列宽 String(40)**（design_pending_review 21 字符超 VARCHAR(20) → MariaDB 1406）。
- **Epic/Story 的 PATCH 只校验取值、不校验迁移**（可自由赋值）。Epic DB CHECK `ck_epics_status` **不含 blocked**（6 值），但 `_check_status` 接受 blocked（ALL_STATUSES 含）→ PATCH epic=blocked 会 IntegrityError，潜在缺陷。Story DB CHECK `ck_stories_status` 9 值，独有 `pending_review`/`ready`（`STORY_REVIEW_STATUSES`）+ blocked。
- **评审闭环（Epic 122，CAS 并发安全，评论=评审意见唯一载体）**：Story `assign_reviewer`（backlog+reviewer NULL→pending_review+reviewer）→ `review_story` approve→ready / reject→round+1 仍 pending_review / `MAX_REVIEW_ROUNDS=5` 达限→blocked；Task `claim_development_task`（backlog/todo→in_progress+assignee，**绕开 TRANSITIONS**）→ `submit_task_for_review`（仅 in_progress 可提交）→ `assign_task_reviewer`（in_review+reviewer NULL，候选≠assignee）→ `review_task` approve→done / reject→退回 in_progress round+1（reviewer 保留复审）/ 达限→blocked。review_mode=majority 时走 `_vote_majority` 多数决。
- Story/Epic 状态 `PATCH /api/{stories|epics}/{id}`；Story 创建 `POST /api/epics/{epicId}/stories`；`TaskIn` 必含 `project_id`。
- CORS：middleware 早返回的 JSONResponse 须 `_apply_cors` 手动补头，否则前端 `0 Unknown Error`。速率限制中间件已移除（`8036b1e`）。

## 访问控制
- 仅 `REQUIRE_AUTH=1`（Docker 默认）生效。`project_access_middleware` 拦所有 `/api` 项目级路由：私有项目仅成员/admin 可见，公开可读但写需成员；项目根 PATCH/DELETE 需 owner/admin；**is_admin 全局绕过**。新增项目级接口勿绕过中间件。
- API Key `abk_` 经 `_current_user()` 解析为完整身份；`/api/admin/*` 走 `_require_admin()`。MCP 用非管理员 key（`make-mcp-token.py` 建 `mcp-service`）。

## 构建 / 部署（关键）
- 前端流程：`export PATH=<managed-node-22.22.2>:$PATH && npm run build` → cp `frontend/dist/frontend/browser/*` → `agentboard/web/static/`（勿用 `node.exe .bin/ng build`）。docker-compose web 绑定挂载 `./agentboard/web/static`、api 绑定挂载 `./agentboard` → 改代码只需 `docker compose restart api web`，无需重建镜像。
- ⚠️ 部署致命坑：`agentboard/web/static` 是 git 跟踪构建产物会滞后于源码。顺序：**git pull → npm run build → cp static → docker compose restart web**，curl `:28080/` 确认 main-*.js hash 已变。static 含手动资源 `mermaid.min.js` 勿删。
- ⚠️ Angular DevTools 红条：`app.config.ts` 的 `provideBrowserGlobalErrorListeners()` 应 `...(isDevMode() ? [...] : [])`。
- 新增 Python 依赖：`docker compose exec api pip install <pkg>`（重启保留）；固化才 `docker compose build api`。
- ⚠️ 实测 api 镜像缺 `croniter`（requirements 已列但构建时未含）→ `scheduler.py` 导入失败；已在运行容器 `pip install croniter`（重启保留），重建镜像才固化。
- Docker 运维：`com.docker.service` 停止时须启动 `Docker Desktop.exe`（会自动拉起全部容器含 mcp 18001）。`agentboard-rabbitmq` 无 restart 策略，重启后需 `docker start`。compose 必填 env 放本地 .env：`AGENTBOARD_CORS_ORIGINS`/`MARIADB_PASSWORD`/`MARIADB_ROOT_PASSWORD`/`AGENTBOARD_WEB_API_URL`。

## Git / 协作
- 文档驱动：需求 `docs/requirements.md`、任务 `docs/tasks.md`、变更 `openspec/changes/<id>/{proposal,design,tasks}.md`。
- **Git 硬规则**：`git add <本次文件>`（勿 add .，工作区常有 dist/记忆文件等预存脏文件）→ commit → **立即 `git push origin main`**；失败须提示用户重试。remote `ssh://git@ssh.github.com:443/hfutlimit/AgentBoard.git`（SSH over 443）。

## MCP（关键坑）
- `MCP_REQUIRE_AUTH=0`（默认）→ 调用方 key 不生效，MCP 固定以服务端 `.env` `AGENTBOARD_MCP_TOKEN` 身份运行；身份错位=换目标用户 abk_ key。
- 连接器 `agentboard` 指向 `http://124.220.44.12/mcp`（远程生产 Windows `C:\AgentBoard`，NSSM）；本机 docker：api=18000 / web=28080 / MCP=18001 仅本地验证。
- `mcp_server.py` 新增工具用 `_http(method,path)`（路径带 `/api`）；`set_status`/`update_story`/`update_epic` 走 helper。注册验证：`asyncio.run(mcp.list_tools())`（FastMCP 无 `_tool_manager._tools`）；直调工具 .fn 须设 `os.environ['AGENTBOARD_MCP_TOKEN']` + `ms.API_URL`；AST 护栏须含 builtins。
- ⚠️ **18001 MCP 容器跑内存中旧代码**（启动后进程内存不变，`_api`→`_http` 修复不重启不生效）。**自动化约束禁止重启 18001**（切断 WorkBuddy MCP 连接）→ 修复靠自包含测试验证，容器重部署留独立运维窗口。`dist/agentboard-*` 是提交进 Git 的旧快照，重建 dist 须同步。

## 自动化任务经验
- **MCP 优先**：AgentBoard MCP 是进度唯一权威来源。`mcp__agentboard__set_status` 沙箱有序列化 bug → 改用 curl REST API 更新状态（本次 2026-08-06 已直接可用，异常时走 REST）。
- 并发锁 `.workbuddy/autodev.lock`（90min 过期）。**禁止触碰端口 18001**。
- E2E 备用登录：`POST /api/auth/register` 建 admin/admin123 用于 Playwright。

## Playwright 验证经验
- 登录：`add_init_script` 注入 `localStorage.agentboard_token`；admin/admin123 可用；venv `C:\Users\jason\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（playwright 1.61.0）。
- E2E 失败请求只计 js/css；`/api/*` ERR_ABORTED 良性。侧栏整树预加载性能债：先 `wait_for_function("!document.querySelector('.skeleton')", timeout=60000)`。
- 深链 goto 有 `tasks()` 信号竞态，必要时 retry/reload；Angular PATCH Observable 不 emit → 前端用 `fetch()`。
- 页面结构速查：任务详情描述 `.two-col .task-md`（无 card/md）；Epic 描述 `.detail-panel .card.md.task-md`；Story 描述 `.story-description`；`/story/{id}` 默认 detail tab，任务列表须点「📝 Task 列表」；评论统一 `.comments-card .md.text-pre`；quick-view 抽屉 `.qv-desc`/`.qv-comment-body`/`.qv-comment-input`；`/epic|/story|/task/{id}` 均可深链。
- 测试产物一律写 `tmp/`（已 gitignore，禁止 `git add` 强追）。
- ⚠️ 仓库 `*_e2e.py` 硬编码目标 `127.0.0.1:8090`（web）/:58125（api），非本机 docker(28080/18000)，无法直接套用；验证本机部署改用自定义 playwright 冒烟（见 tmp/e2e_smoke.py）。

## 评论三实体（2026-08-05，`9e70415`）
- `comments` 表 task_id/story_id/epic_id 恰好其一非空（迁移 `n1o2p3q4r5s6`；SQLite 改列约束须 `batch_alter_table`）。
- 端点：`GET/POST /api/{tasks|stories|epics}/{id}/comments`；删除 `DELETE /api/comments/{cid}`。service `create_comment`/`list_comments` 已 keyword-only（task_id=/story_id=/epic_id=），位置调用会 TypeError。
- 前端 Story/Epic 评论区复用 `.comments-card`，模板变量 `epAuthor/epContent/stAuthor/stContent`；发布后只重载评论列表，勿用 `run()`（重置 storyTab/epicTab）。

## Proposal 闭环（2026-08-04 Epic 96 收尾）
- 状态机 converged→story_created 终态；转化 `POST /api/proposals/{pid}/convert {epic_id, title?}`；幂等：story_id 已回填+Story 在 → 复用。
- proposals 是**项目级 Tab**（`/project/{pid}/proposals`）。Story 子任务列表 `GET /api/stories/{sid}/tasks` 返回 `{items,total}`。

## 文档模块
`DOCUMENT_TYPES=['memory','plan','knowledge','design']`；项目 Tab 内就地更新，`createDocument/deleteDoc` 默认跳 `/documents`。
- 文件夹：`document_folders` 表（parent_id 自引用）+ `documents.folder_id`（FK SET NULL）。`DocumentPatch.folder_id` 显式 null=移出根（`model_fields_set` 判断，`exclude_none` 吞 null）；删除文件夹=子项上提父级。拖拽 payload **三通道**：window `__agentboardDrag` → dataTransfer 自定义 MIME → 组件信号（drop 阶段 getData 不可靠）；`_docDropBusy` 防重入。

## COS 上传（2026-08-06 Epic 64 S1，`ce48d59`）
- 纯标准库 `agentboard/cos_client.py`，零第三方依赖。COS V5 四步签名：`StringToSign = sha1\n{KeyTime}\n{SHA1(HttpString)}\n`（**非**直接对 HttpString 做 HMAC）；SignKey=HMAC-SHA1(SecretKey, KeyTime)，Signature=HMAC-SHA1(SignKey, StringToSign)。
- HttpParameters/Headers 按 key 字典序，值 URL 编码保留 `;/:=+,-_.~`；`parse_qs` 解析空值加 `keep_blank_values=True`；`urllib.request.Request.headers.items()` 返回 capitalize 后 header 名。
- 端点 `GET/POST /api/projects/{pid}/cos/config|upload`（env 占位 + 未配置 503/configured:false 优雅降级 + 10MB 上限 + 图片 MIME 白名单），权限走 project_access_middleware 自动覆盖。

## Dashboard 跨项目聚合统计（2026-08-06 Epic 117，`3844a99`）
- 根因：`loadDashboard` 四级整树级联（projects→epics→stories→tasks）请求量爆炸（远程生产数百个），骨架屏等待长（已知性能债）。
- 端点 `GET /api/overview`（agentboard/service.py get_overview + api.py）：一次返回 `counts / projects / status_distribution / activity_7d`，可见性复用 list_accessible_projects（admin 全量/普通用户成员项目/未登录空），SQLAlchemy 条件聚合单次往返取状态分布与项目进度。
- 前端两阶段渲染：loadDashboard 先 overview 驱动统计卡秒出（loading 提前结束），后台 void loadDashboardFullTree 填充 epics/stories/tasks 全局信号（搜索/看板契约不变）。overview 失败/null 时 computed 回退 tasks()。
- api.service getOverview 15s TTL 缓存；stats-row 改读 statProjects/Epics/Stories/Tasks computed；图表 4 个 computed（dashboardStatusChart/dashboardProjectProgress/dashboardActivity/doneTasks）overview 优先。
- 单测 tests/test_overview.py（结构/权限/一致性/API 直调 3 passed）；E2E tests/test_overview_e2e.py（首页秒出 + 0 报错 + 项目页回归）。

## Windows/IIS 生产
IIS(ARR) 反代 WebAPI:8000 / MCP:8001（NSSM），MariaDB，前端 IIS 托管（web.config /api /mcp 反代 + SPA 回退）。打包 `scripts/package_windows.py`；两端 `AGENTBOARD_SECRET` 须一致。生产 `http://124.220.44.12/`。
