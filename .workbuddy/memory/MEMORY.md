# AgentBoard 项目长期记忆

## 项目定位
轻量项目管理工具 + OpenSpec/规范驱动开发。层级：Project → Epic → Story → Task/Bug。任务挂载 `description`/`spec`（markdown），经 MCP/REST 暴露给 AI Agent。存储双后端：SQLite 调试 / MariaDB 生产（`AGENTBOARD_DB_URL` 切换）。

## 前端优化长期轨道
- 来源：用户要求"持续优化前端、模仿 Jira、每次只做小的优化"。
- 权威清单：`docs/tasks.md`（Epic 11 及后续）；process 容器：`openspec/changes/frontend-continuous/`。
- **迭代纪律**：每周期只交付**一项**小优化；单文件改动为主；新增前端代码 < ~80 行；不引入新框架/打包依赖；**不改 `models.py`/`api.py` 契约**。
- commit 规范：`feat(ui): 前端小优化 - <描述>`。

## 已完成 Epic 汇总
- **Epic 8/15/22/23/24** → done（v0.4~v0.6 体验优化、审计日志、任务依赖、Webhook、缓存、Toast、移动端、通知图标、收藏/最近访问）。
- **Epic 16 (前端体验升级 v1.2)** → done（commit `fdc376c`，2026-07-17）：任务详情页增强（面包屑/负责人/子任务进度条/相关任务链接）、评论预览/成员头像/空项目引导。
- **Epic 33 (前端体验升级 v1.3)** → done（commit `db0b209`，2026-07-17）：Epic 进度条可视化、Task 快速复制。
- **Epic 34 (前端体验升级 v1.4)** → done（commit `4e7b4f2`，2026-07-18）：任务列表汇总栏（状态分布堆叠条 + 完成率文案）。
- **Epic 103 (看板拖拽 B-04)** → done（commit `4a486cf`，2026-07-17）：Story 看板 HTML5 drag-and-drop。
- **项目 3 (AgentBoard) Backlog 全清零**（2026-07-17）：109 任务 100% done。
- **A-22 任务快速完成勾选** → done（2026-07-17）：列表 + 看板快速完成按钮，后端状态机放宽 TODO/IN_PROGRESS→DONE、DONE→TODO。
- **Task 831 列表密度切换** → done（2026-07-16）：紧凑视图切换。
- **Epic 35 (前端体验升级 v1.5)** → done（commit `1f70841`，2026-07-18）：任务关键词搜索——`taskSearchQuery` signal + 工具条搜索输入框 + `visibleTasks` 叠加 title/description 过滤。
- **Epic 36 (前端体验升级 v1.6)** → done（commit `257c654`，2026-07-18）：内联任务标题编辑——hover 显示 ✎ 编辑按钮 → inline input，Enter/Esc/blur 控制；`saveInlineEdit()` 用 `fetch()` 绕过 Angular HttpClient PATCH 不返回问题。
- **B-06 / Epic 28 v1.7 (前端体验升级 v1.7)** → done（2026-07-18）：任务列表分组（按状态/类型/负责人）；`taskGroupBy` signal + `groupedTasks` computed + `<select>` 切换 + localStorage 持久化。关键修复：未指派任务 `assignee_id=null` 经空串 key 被 `@if(grp.key)` 吞掉「未指派」标题——改 `'unassigned'` 哨兵真值键。DB Epic 28(28)/Story 64/Task 836 全 done。
- **Epic 29 v1.8 (可折叠任务分组)** → done（commit `3e39c2e`，2026-07-19）：分组后组标题可点击折叠/展开；`collapsedGroups` signal（`Set<string>`）+ `toggleGroup()` + chevron（▸/▾）+ `@if` 包裹组内 `@for` 控制可见性 + localStorage `agentboard_collapsed_groups` 持久化。angular.json CSS budget 36kB→40kB。DB Epic 29/Story 65/Task 837 全 done。

## 协作与发布约定
- **文档驱动**：需求 `docs/requirements.md`、任务 `docs/tasks.md`、变更 `openspec/changes/<id>/{proposal,design,tasks}.md`。
- **Git 硬规则**：每次修改后 `git add . && git commit -m "feat: ..."` 并**立即 `git push origin main`**；push 失败需提示用户本地重试，不得静默跳过。
- **Git push SSH over 443**（网络固定限制）：`git remote set-url origin ssh://git@ssh.github.com:443/hfutlimit/AgentBoard.git`，认证用户 `Jzhong2026`。
- **前端流程**：改前端 → `npm run build` → cp 到 `agentboard/web/static/` → Playwright 验证（Page Errors、404 Resources、Angular 渲染）。无需 docker rebuild（static 挂载即时生效）。
- **部署**：Docker 改后端需 `docker cp` 注入 + `docker restart`；本地 uvicorn (58125) + web (8080) 用于开发验证。

## API/状态机约定
- 任务改状态用 `PUT /api/tasks/{tid}/status` body=`{"status":"..."}`；**真实迁移表**（`service.py` TRANSITIONS，L30-37）：`BACKLOG→{TODO}`、`TODO→{IN_PROGRESS,BACKLOG,DONE}`、`IN_PROGRESS→{IN_REVIEW,VERIFYING,TODO,DONE}`、`IN_REVIEW→{DONE,IN_PROGRESS}`、`VERIFYING→{DONE,IN_PROGRESS}`、`DONE→{IN_PROGRESS,TODO}`。**注意：无 `in_review → verifying` 边**（旧记忆里的线性链是错的），从 in_review 只能直接→done 或回 in_progress。状态同步脚本应在 TRANSITIONS 上 BFS 求最短合法路径，勿硬编码线性顺序。**例外（A-22 快速完成）**：允许 `TODO/IN_PROGRESS→DONE` 与 `DONE→TODO`，`IN_PROGRESS→BACKLOG` 仍禁止。
- Story/Epic 改状态用 `PATCH /api/{stories|epics}/{id}` body=`{"status":"..."}`。
- `TaskIn` 必须含 `project_id`；`TaskPatch` 含 `sprint_id`（可 null）。
- CORS：`require_business_auth` 对 401 响应手动注入 `Access-Control-Allow-Origin`（`agentboard/api.py` L49-55）。后端速率限制中间件已于 2026-07-20 移除（commit `8036b1e`）。

## 自动化任务经验（关键）
- **MCP 优先**：AgentBoard MCP 是进度唯一权威来源。`mcp__agentboard__set_status` 沙箱有序列化 bug → 改用 curl REST API 更新状态。
- **MCP auth 不可用**：`auth_login` 返回 token 后 `list_projects` 仍 unauthorized。备选：`POST /api/auth/register` 创建 admin/admin123（id=54）用于 Playwright E2E 登录。
- **`mcp_server.py` 既有缺陷**：`_api` 未定义，15 个既有工具调用即 NameError/404。新增工具一律用 `_http(method, path, ...)`（路径带 `/api`）。修复建议：`_api = _http` 并补全路径前缀（暂未修）。
- **多 DB 注意**：本地 uvicorn (58125) 用 `agentboard.db`（root，数据完整），Docker API (18000) 用不同 DB。Playwright 测试须用 8080 端口。
- **并发锁**：自动开发前检查 `.workbuddy/autodev.lock`；90 分钟内存在则停，否则建锁、结束删锁。
- **禁止触碰端口 18001**：WorkBuddy MCP 通信端口，任何 docker 操作不得影响。

## 项目访问控制架构（2026-07-20 加固）
- 仅 `REQUIRE_AUTH=1`（Docker 默认）下生效；本地开放模式（`REQUIRE_AUTH=0`）不强制。
- `project_access_middleware`（`agentboard/api.py`）统一拦截所有 `/api` 项目级路由：按路径/子资源 id/query 解析目标 project；**私有项目仅成员/系统管理员可见**，公开项目可读但写入需成员；项目根 PATCH/DELETE 需 owner 或管理员；**系统管理员 `is_admin` 全局绕过**。
- 子资源→project 解析器在 `service.py`（`get_epic_project_id`/`get_story_project_id`/`get_task_project_id` 等）。`create_project` 支持 `is_private`（前端新建弹窗默认勾选「私有项目」）。
- 新增/修改项目级接口时勿绕过该中间件（已覆盖 epics/stories/tasks/sprints/schedules/webhooks/评论/附件/统计/导出）。`/api/admin/projects` 需用系统管理员 token；`list_all_projects_admin` 内曾因 `func` 未导入 500，已改为 `.count()`。

## Playwright 验证经验
- **登录流程**：无 `localStorage.agentboard_token` 时 SPA 重定向到 `/login`；脚本里先走注册流程（点 `.auth-tab` 注册、填 `input[name=username]/[name=password]`、提交 `.login-submit`）。
- **导航方式**：点击侧栏 nav 导航（不直接 `goto` URL，Angular 路由 `loadRoute` 不总触发）。
- **选择器歧义**：`☰` 按钮有两个（`#sidebar-toggle` 侧栏 / `#s-density-toggle` 密度），必须按 `id` 精确选择。
- **web 服务**：用 `agentboard/web_app.py`（STATIC_DIR 解析为 `agentboard/web/static`）；根目录 `web_app_new.py` 路径错误。`web_app.py` 注入 `window.AGENTBOARD_API`（本地 `http://127.0.0.1:58125`）。
- **failed-request 过滤约定**：E2E 只计 `.js`/`.css` 失败请求；`/api/*` 的 `ERR_ABORTED`（dashboard 预加载竞态 / 导航中断）是既有良性现象。
- **`tasks()` 信号 SPA 路由竞态**（既有 bug）：直接 `page.goto(/story/N)` 时 `tasks()` 可能含全项目任务而非 story 级。根因：`loadRoute()` 首次触发 `loadDashboard()` 预加载全量 tasks，慢，覆盖 story 级 tasks。修复需在 `loadRoute` 加守卫，超出单次范围，记录为后续项。
- **managed python venv playwright**：`C:\Users\jason\.workbuddy\binaries\python\envs\default\Scripts\python.exe` 已装 playwright 1.61.0；Chromium 缓存于 `~/AppData/Local/ms-playwright`。
- **前端构建部署（无需 docker rebuild）**：`npm run build`（managed node 22.22.2）→ `frontend/dist/frontend/browser/` → cp 到 `agentboard/web/static/`；两端即时生效。
- **构建命令坑（2026-07-20 实测）**：**勿用 `node.exe node_modules/.bin/ng build`**——`ng` 是 shell wrapper，被当 JS 解析报 `SyntaxError`。必须 `export PATH=<managed-node>:$PATH && npm run build`，由 npm 调度 ng 脚本。
- **组件作用域 CSS（2026-07-20 实测）**：`frontend/src/app/app.css` 是**组件作用域**样式，编译进 `main-*.js` 由运行时注入；`grep dist/styles-*.css` 查不到其规则属正常。验证某规则是否编译进产物应 `grep dist/main-*.js <rule>`（如 `search-kbd` count=1）。
- **Angular HttpClient PATCH 不返回**（2026-07-18 实测）：`this.api.updateTask()` 用 `http.request('PATCH', ...)` 返回的 Observable 不 emit（request 发出但 response 不触发 next/complete），GET/PUT 均正常。**Workaround**：直接用 `fetch()` 调 API，绕过 HttpClient。
- **angular.json font inlining 间歇失败**（2026-07-18）：Google Fonts `@import` 在构建时被 CSS optimizer 试图内联，网络不可达时构建失败。**修复**：`angular.json` production 配置加 `"optimization": {"fonts": false}`。
- **web_app.py SPA fallback 有效**：`page.goto(f"{BASE}/story/33")` 直接触发 Angular Router `loadRoute()`，无需 hash 导航或侧栏点击。

## 文档模块前端 API 约定（复用要点）
- 既有 signals：`documents`/`docItem`(Signal<DocumentItem|null>)/`docCommentPreview`(**boolean** 全局预览开关)/`docCommentContent`(string)/`docFilterType`/`docFilterStatus`/`docSearchQuery`/`docTypes`/`docStatuses`。
- 方法签名（易错）：`toggleDocCommentPreview()` **0 参**；`addDocComment(event)` 读 `docCommentContent()` 信号；`openDocEdit()` **0 参**（操作 `docItem()`）；`docTypeLabel(t: DocumentType)`/`docStatusLabel(s: DocumentStatus)` 入参为枚举值字符串；`openDocTab(d)` 写入 docItem+加载评论（不走路由，供 Tab 行点击）。
- `createDocument`/`deleteDoc` 默认会 `router.navigateByUrl('/documents...')` 跳走——**在 Tab 内使用时需改为就地更新 `documents()` 列表、用 `view()==='project' && activeTab()==='documents'` 判断上下文是否跳转**。
- `onDocFilterChange()`/`onDocSearchChange()` 会调 `loadDocuments()` 重拉**全量**文档（不按 project_id），Tab 内改用客户端筛选（`projectDocVisible` 读 filter 信号），勿触发这两个函数。
- `DOCUMENT_TYPES = ['memory','plan','knowledge','design']`（无 `guide` 等）。
- 模板控制流：`} @else {` 必须带 `@`，写 `} else {` 会 NG 编译失败（EOF/parse）。

## Windows/IIS 原生部署（非 Docker）
- 拓扑：IIS(ARR) 统一反代，WebAPI 127.0.0.1:8000、MCP 127.0.0.1:8001，均 NSSM 服务；DB MariaDB。前端静态由 IIS 直接托管，`web.config` 含 `/api`、`/mcp` 反代 + SPA 回退。
- 打包：`scripts/package_windows.py` → `dist/*.zip`（webapi/mcp/web 三份）。运行时脚本：`scripts/deploy/`。文档：`docs/deploy-windows-iis.md`。
- **MCP 鉴权硬约束**：生产 `REQUIRE_AUTH=1` 下，MCP 必须经 `abk_` API Key 调 API；用 `make-mcp-token.py` 生成（权限 `["api:*"]`），填 `AGENTBOARD_MCP_TOKEN`，且两端 `AGENTBOARD_SECRET` 必须一致。
- 前端 `index.html` 资源相对引用，IIS 站点根 = 静态目录；`__API_URL__` 默认 `/api`（同源反代），由 `configure-api-url.ps1` 注入。
- 服务器需装：Python 3.13、MariaDB、IIS + URL Rewrite + ARR（并 Enable proxy）、NSSM。无需服务器装 Node。
