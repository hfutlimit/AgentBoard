# AgentBoard 项目长期记忆

## 项目定位
轻量项目管理工具 + OpenSpec/规范驱动开发。层级：Project → Epic → Story → Task/Bug。任务挂载 `description`/`spec`（markdown），经 MCP/REST 暴露给 AI Agent。存储双后端：SQLite 调试 / MariaDB 生产（`AGENTBOARD_DB_URL` 切换）。

## 前端优化长期轨道
- 来源：用户要求“持续优化前端、模仿 Jira、每次只做小的优化”。
- 权威清单：`docs/tasks.md`（Epic 11 及后续）；process 容器：`openspec/changes/frontend-continuous/`。
- **迭代纪律**：每周期只交付**一项**小优化；单文件改动为主；新增前端代码 < ~80 行；不引入新框架/打包依赖；**不改 `models.py`/`api.py` 契约**。
- commit 规范：`feat(ui): 前端小优化 - <描述>`。

## 能力现状
- **Epic 8（前端体验升级与平台增强 v0.6）→ done** ✅（commit `a10a593`，2026-07-16）
  - Story 25.1-25.4 / Task 600-605：看板卡片优先级色边框、完成进度条、任务列表高级筛选、抽屉快速操作、通知折叠分组、全局快捷键提示。
  - Playwright 验证通过：/story/19 看板渲染 36 张卡片，零 JS/控制台/404 错误。
- **Task estimate 后端 → done** ✅：`tasks.estimate` 列 + Alembic 迁移 `e1f2a3b4c5d6` + REST CRUD/PATCH + `tests/test_task_estimate.py`。
- **Epic 15 (v0.4+ 体验优化) → done** ✅：通知类型图标、收藏/最近访问。
- **Epic 22/23/24 → done** ✅：审计日志、任务依赖、Webhook、缓存、Toast、移动端。
- **Sprint 功能就绪**：planning/active/completed + 单 active 约束 + 任务退回 backlog。
- **Task 831 列表密度切换（紧凑视图）→ done** ✅（2026-07-16）：`listDensity` signal（`localStorage` 持久化）+ 任务列表工具条 `☰` 切换按钮（`#s-density-toggle`）+ `.entity-list.density-compact` 类 + 紧凑 CSS（行内边距/字号收敛）；净增 ~24 行，符合 R2。Playwright 验证：切换前后 `.entity-item--rich` padding 10px→6px，零错误。
- **顺带修复**：`frontend/src/index.html` 遗留 `<link href="/static/style.css">` 孤儿引用（每次构建复现 404 + 构建告警）；已从源码移除，Angular 注入的 `styles-*.css` 接管。

## 协作与发布约定
- **文档驱动**：需求 `docs/requirements.md`、任务 `docs/tasks.md`、变更 `openspec/changes/<id>/{proposal,design,tasks}.md`。
- **Git 硬规则**：每次修改后必须 `git add . && git commit -m "feat: ..."` 并**立即 `git push origin main`**；push 失败需提示用户本地重试，不得静默跳过。
- **前端流程**：改前端 → 重建/复制 static → Playwright 验证（Page Errors、404 Resources、Angular 渲染）。
- **部署**：Docker 环境下前端改动需 `docker compose up -d --build`；沙箱无 Docker 时改用本地 uvicorn + SQLite（`data/agentboard.db`）。浏览器务必硬刷新清静态缓存。

## API/状态机约定
- 任务改状态用 `PUT /api/tasks/{tid}/status` body=`{"status":"..."}`，状态机：`backlog → todo → in_progress → in_review → verifying → done`（不可跳跃）。
- Story/Epic 改状态用 `PATCH /api/{stories|epics}/{id}` body=`{"status":"..."}`。
- `TaskIn` 必须含 `project_id`；`TaskPatch` 含 `sprint_id`（可 null）。
- CORS：中间件 `require_business_auth` 对 401 响应手动注入 `Access-Control-Allow-Origin`（`agentboard/api.py` L49-55）。

## 自动化任务经验
- **MCP 状态更新绕过**：沙箱 `mcp__agentboard__set_status` 有 DeferExecuteTool 序列化 bug，改用 curl 直接调 REST。
- **多 DB 注意**：运行中的 API 可能连接 `agentboard.db`（根）或 `data/agentboard.db`；验证/状态流转前务必确认 `AGENTBOARD_DB_URL` 指向目标库。
- **容器代码同步**：容器内 `/app/agentboard/api.py` 可能与本地不同步；用 `docker exec grep` 验证，必要时 `docker cp` 注入。
- **并发锁**：自动开发前检查 `.workbuddy/autodev.lock`；90 分钟内存在则停，否则建锁、结束删锁。
- **Playwright 验证坑（2026-07-16 实测）**：① SPA 路由守卫——无 `localStorage.agentboard_token` 时 `http://.../story/19` 会被重定向到 `/login`；必须在脚本里先走「注册」UI 流程建会话（点 `.auth-tab` 注册、填 `input[name=username]/[name=password]`、提交 `.login-submit`），再导航。② 选择器歧义——带 `☰` 图标的按钮有两个：侧栏开关 `#sidebar-toggle`（class `icon-btn`）与密度切换 `#s-density-toggle`（class `ghost-sm`）；用 `button,has_text=☰` + `.first` 会误点侧栏，必须按 `id` 精确选择。③ web 服务用 `agentboard/web_app.py`（其 STATIC_DIR 解析为 `agentboard/web/static`）；根目录 `web_app_new.py` 的 STATIC_DIR 解析错误会报 `Directory does not exist`。④ 新 Python venv 需 `pip install playwright`；Chromium 已缓存于 `~/AppData/Local/ms-playwright`，无需再下载。
- **Docker web_app.py 不同步（2026-07-16 #2 实测）**：容器内 `web_app.py` 可能是旧版（不做 `/static/` 路径重写），导致 Angular JS/CSS 404、页面空白。需 `docker cp agentboard/web_app.py agentboard-web-1:/app/agentboard/web_app.py` 注入新版。
- **Rate limiter 阻断 CORS preflight**：API 容器 rate limiter 对 Docker bridge IP（非 127.0.0.1）生效，OPTIONS preflight 返回 429 → CORS 失败。重启 API 容器可临时清除计数。
- **Playwright E2E 基础设施已损坏（2026-07-16 #2）**：现有 `test_playwright_e2e.py` 全部 6 项 FAIL（选择器 `#login-btn` 已不存在，登录页重构为 Angular 组件 `app-login`，使用 `.auth-tab` + `.login-submit`）。需修复后才能跑 E2E。
- **MCP auth 仍不可用**：`mcp__agentboard__auth_login` 返回 token 后，`mcp__agentboard__list_projects` 仍 unauthorized。改用 REST API + curl/Python urllib 更新状态。
