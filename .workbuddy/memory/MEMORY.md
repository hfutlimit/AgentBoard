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

## 既有能力现状（2026-07-10 盘点，Epic 7 于 2026-07-12 完成）
- **鉴权全链路就绪**：后端 `/api/auth/register|login|me` + `users` 表 + 测试；**前端登录/注册 UI 已完成（Epic 7，2026-07-12）**。`app.js` 含 `getToken/setToken/clearToken` + `CURRENT_USER`；`api()` 注入 `Authorization` 并在 401 跳登录（auth 端点自身不触发，避免递归）；顶栏用户名+登出。
- **Epic 7 启动守卫为"动态"设计（重要）**：后端开放（默认 `AGENTBOARD_REQUIRE_AUTH` 未设/0）时免登可用；一旦后端设为 `1` 强制鉴权，SPA 数据请求 401 会**自动跳登录**。据此**不要**把 SPA 改成"无 token 一律硬拦截进入"——会破坏当前开放部署。如需强制登录，应在后端置 `AGENTBOARD_REQUIRE_AUTH=1`，SPA 会自动适配。
- MariaDB：切换/驱动/Alembic/`db` profile 就绪；缺独立 `.sql` 脚本与真实验证（Epic 8）。
- Playwright：无（Epic 9）。MCP 服务完整，待鉴权集成+运维化（Epic 10，已完成）。

## 协作流程约定
- 文档驱动：需求 `docs/requirements.md`、主任务 `docs/tasks.md`（Epic 分段）、每个变更 `openspec/changes/<id>/{proposal,design,tasks}.md`。
- Git（⚠️ 本条为本项目硬性约定）：**每次修改都要及时 push**。无论改动大小（含文档/任务拆分/数据库运行时以外的任何文件变更），完成 `git add . && git commit -m "feat: ..."` 后**必须立即 `git push origin main`**。push 若失败（沙箱 SSH 受限）需提示用户本地重试，不得静默跳过。

## 部署约定（重要，踩坑记录 2026-07-10）
- **前端改动必须重建镜像**：`web_app.py` 从 `agentboard/web/static/` 读文件，但 Dockerfile 用 `COPY . .` 把源码**构建时**烤进镜像；`docker-compose.yml` 的 web/api 服务**只挂了 `agentboard_data` 数据卷，没挂源码**。因此改了静态文件后，只跑 `docker compose up -d` 会复用旧镜像 → 看到老页面。
- **正确重部署命令**：`docker compose up -d --build`（或先 `docker compose build` 再 `up -d`）。本次已在沙箱执行 `docker compose up -d --build web` 修复"看不到新前端"问题。
- Web 端口 **8080**（非 5080），API 端口 8000。浏览器访问 http://localhost:8080 ，SPA 经 `AGENTBOARD_API_URL`（默认 localhost:8000）调 API。
- 部署后浏览器务必**硬刷新**（Ctrl/Cmd+Shift+R）清静态缓存。
