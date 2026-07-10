# AgentBoard 任务列表

> 工作清单（Superpowers / OpenSpec 风格）。每个 Epic 对应一个能力，Story 对应一个可交付切片，Task 为可执行单元。
> 约定：`[ ]` 未开始，`[x]` 已完成。状态参考 `docs/requirements.md` FR-5。
> 开发顺序建议自上而下；Epic 6 用于以本项目的规范来管理本项目自身。

---

## Epic 1：数据模型与存储层
> 目标：可切换的双后端（SQLite / MariaDB）存储，承载项目树。

### Story 1.1 定义数据模型
- [ ] Task：绘制 ER 模型并固化到 `docs/requirements.md` §6
- [ ] Task：实现 SQLAlchemy 模型（Project / Epic / Story / Task）
- [ ] Task：定义状态枚举与合法迁移规则

### Story 1.2 存储抽象与切换
- [ ] Task：基于 `AGENTBOARD_DB_URL` 实现数据库引擎工厂（SQLite / MariaDB）
- [ ] Task：编写连接池与健康检查
- [ ] Task：Alembic 初始化与首版迁移脚本
- [ ] Task：存储层单元测试（SQLite 下跑通，验证 MariaDB DDL 兼容）

---

## Epic 2：核心服务层（CRUD）
> 目标：业务服务封装，供 MCP 与未来 UI 复用。

### Story 2.1 项目树 CRUD
- [ ] Task：`create_project / get_project / list_projects / update_project / delete_project`
- [ ] Task：`create_epic / get_epic / list_epics`
- [ ] Task：`create_story / get_story / list_stories`
- [ ] Task：`create_task`（type=task|bug）/ `get_task` / `list_tasks`
- [ ] Task：级联删除策略（Project 删除时处理子节点）

### Story 2.2 描述与规范读写
- [ ] Task：`set_task_description` / `get_task_description`
- [ ] Task：`set_task_spec` / `get_task_spec` / `append_task_spec`
- [ ] Task：spec 内容校验（markdown 解析不报错即接受）

### Story 2.3 查询、过滤与搜索
- [ ] Task：按 project / epic / story / type / status 过滤
- [ ] Task：关键字搜索 title / description / spec
- [ ] Task：服务层单元测试

---

## Epic 3：MCP 服务
> 目标：暴露工具给 AI Agent，驱动开发闭环。

### Story 3.1 基础 CRUD 工具
- [ ] Task：FastMCP 服务骨架 + 启动入口
- [ ] Task：注册项目树 CRUD 工具
- [ ] Task：注册 task 描述 / spec 读写工具
- [ ] Task：注册搜索 / 过滤工具
- [ ] Task：注册状态流转工具（校验合法迁移）

### Story 3.2 工具契约与返回结构
- [ ] Task：统一 JSON 返回结构（含 id / 错误信息）
- [ ] Task：MCP 工具入参 schema 校验
- [ ] Task：本地以 MCP 客户端联调（SQLite）

---

## Epic 4：OpenSpec / Superpowers 类能力（特色）
> 目标：把规范驱动开发留在任务里。

### Story 4.1 Spec 模板
- [ ] Task：定义 change-proposal 模板（背景 / 目标 / 范围 / 任务清单 / 验收）
- [ ] Task：提供"生成变更提案"工具，写入 task.spec
- [ ] Task：定义执行计划（plan）模板

### Story 4.2 规范与任务联动（可选）
- [x] Task：从 spec 解析清单项（- [ ] 标题）并批量建同级子 task（generate_tasks_from_spec）
- [x] Task：task 与 spec 双向引用（子任务记录 source_spec_id，源 spec 回写链接）
- [ ] Task：状态联动（spec 进入 review 时关联 task 转 in_review）

---

## Epic 5（可选）：REST API + Web UI
> 目标：人类可用的可视化管理。

### Story 5.1 REST API
- [ ] Task：FastAPI 暴露核心 CRUD
- [ ] Task：与 MCP 共用同一 service 层

### Story 5.2 简易 Web UI
- [ ] Task：项目树浏览
- [ ] Task：任务详情 + markdown 渲染（description / spec）
- [ ] Task：状态切换交互

---

## Epic 6：以本项目规范管理本项目
> 用 OpenSpec 结构沉淀 AgentBoard 自身的规格与变更。

### Story 6.1 规范目录
- [x] Task：建立 `openspec/specs/agentboard/spec.md`（能力规格 = 当前事实来源）
- [x] Task：建立 `openspec/changes/` 目录 + `openspec/AGENTS.md`（Agent 指引）
- [x] Task：将本 `docs/tasks.md` 与 OpenSpec `tasks.md` 对齐（后续变更走 `openspec/changes/*/tasks.md`）

### Story 6.2 首个变更提案
- [x] Task：以 OpenSpec 提案形式描述后续变更（见 `openspec/changes/mariadb-alembic/`）
- [ ] Task：MariaDB 接入 + Alembic 迁移 + MCP 工具补全（进行中，见 change 的 tasks.md）

---

## 验收总览
- [ ] SQLite 下完整跑通 CRUD + spec 读写 + 搜索
- [ ] MCP 客户端可连接并操作
- [ ] 切换 `AGENTBOARD_DB_URL` 到 MariaDB 后迁移可应用、功能一致
- [ ] `docs/requirements.md` §8 开放问题已确认或记录决策

---

## 开发进度（首次实现 · 2026-07-10）

本轮完成 MVP 骨架并通过 smoke test：

- [x] **Epic 1** 数据层：模型 + SQLite/MariaDB 切换（`create_all` 初始化，Alembic 留待后续）
- [x] **Epic 2** 服务层：项目树 CRUD、级联删除、spec 读写、搜索、状态机校验
- [x] **Epic 3** MCP：FastMCP 工具集（CRUD / spec / 搜索 / 状态 / `spec_proposal`）
- [x] **Epic 4（基础）** spec 模板：`spec_proposal` 生成 OpenSpec 风格提案；Web UI 可编辑 spec
- [x] **Epic 5** Web UI：项目树浏览 + 任务详情 + markdown 渲染 + 状态切换（服务端渲染，无独立 SPA）

### 重构（前后端分离 · 2026-07-10）
- [x] API / Web / MCP 三端拆分为独立服务
- [x] `api.py` 改为纯 REST/JSON + CORS（不再服务端渲染）
- [x] `web_app.py` + `web/static/` 独立 SPA，fetch 调 API
- [x] MCP 支持 `api`（httpx 调 API）/ `db`（直连）双后端
- [x] Web UI 支持对 project/epic/story/task/bug 全部增删改 + 状态流转 + spec 编辑
- [x] smoke test 覆盖四端

待办：
- [ ] Epic 4.2：从 spec 解析子任务 / 双向引用（可选）
- [ ] Epic 6：以 OpenSpec 目录（`openspec/`）管理本项目自身规格
- [ ] Epic 7：前端注册 / 登录 UI（后端鉴权已就绪，仅前端集成）
- [ ] Epic 8：MariaDB 独立 `.sql` 脚本 + 真实集成验证（Alembic 迁移已完成）
- [ ] Epic 9：前端 Web 自动化测试（Playwright 真实浏览器 E2E）
- [ ] Epic 10：MCP 鉴权集成 + 运维化（"实现 MCP"）
- [ ] Epic 11：持续前端优化（模仿 Jira，小步迭代，长期轨道）

---

## Epic 7：前端注册 / 登录（鉴权 UI）
> 目标：补齐 SPA 的登录 / 注册界面与 token 生命周期。后端接口已就绪（见 `openspec/changes/auth/`）。

### Story 7.1 前端鉴权骨架
- [ ] Task：`app.js` 增加 `getToken/setToken/clearToken`（localStorage）
- [ ] Task：改造 `api()` 自动注入 `Authorization`；收到 401 清 token 回登录
- [ ] Task：`index.html` 预留登录 / 注册容器

### Story 7.2 登录 / 注册界面
- [ ] Task：`renderAuth()`：用户名 / 密码 + 登录/注册切换，调用 `/api/auth/register|login`
- [ ] Task：成功存 token 进应用；失败（409/401）展示错误
- [ ] Task：启动守卫：有 token 且 `/api/auth/me` 通过则进应用，否则显示登录

### Story 7.3 应用内用户态
- [ ] Task：顶部栏显示当前用户名 + 登出按钮
- [ ] Task：`style.css` 补充登录卡片 / 用户信息条样式
- [ ] Task（可选）：`api.py` 增加 `AGENTBOARD_REQUIRE_AUTH` 强制 CRUD 鉴权

---

## Epic 8：MariaDB 数据库脚本与集成
> 目标：独立 `.sql` 脚本 + 真实集成验证。Alembic 迁移与 MCP 工具已就绪（见 `openspec/changes/mariadb-alembic/`）。

### Story 8.1 独立 MariaDB 脚本
- [ ] Task：新增 `scripts/mariadb/schema.sql`（建库 utf8mb4、建用户授权、五表与 `models.py` 对齐、含 `source_spec_id`）
- [ ] Task：`scripts/mariadb/README.md` 说明初始化与离线评审用法

### Story 8.2 真实集成验证
- [ ] Task：用户提供 MariaDB 连接信息（`AGENTBOARD_DB_URL=mysql+pymysql://...`）
- [ ] Task：验证 Alembic `upgrade head` 在 MariaDB 11 建表 DDL 兼容
- [ ] Task：MariaDB 下跑通 service 层冒烟（CRUD + 状态机 + 搜索 + 生成子任务）
- [ ] Task：更新 `docker-compose.yml` 的 `db` profile 与 API 对接示例

### Story 8.3 集成测试
- [ ] Task：新增 `tests/test_mariadb_integration.py`（`skipif` 无 `AGENTBOARD_TEST_MARIADB`）

---

## Epic 9：前端 Web 自动化测试（Playwright）
> 目标：真实浏览器驱动 SPA 的 E2E。与 `test_web_flow.py` 互补（见 `openspec/changes/playwright-e2e/`）。

### Story 9.1 测试骨架
- [ ] Task：`requirements.txt` 增加 `playwright` / `pytest-playwright`
- [ ] Task：新增 `tests/test_playwright_e2e.py`：fixture 启动真实 API + Web（临时 SQLite）
- [ ] Task：UI 辅助函数 `ui_register / ui_login`

### Story 9.2 真实交互用例
- [ ] Task：注册 UI 流（进入应用 + localStorage 含 token）
- [ ] Task：登录 UI 流 + 错误密码 / 重复注册报错
- [ ] Task：项目树 CRUD UI（Project→Epic→Story→Task/Bug）
- [ ] Task：状态流转 UI（徽标更新）
- [ ] Task：spec 编辑与 markdown 渲染
- [ ] Task：README 补充 `playwright install chromium` 与运行命令

---

## Epic 10：MCP 鉴权集成与运维化（实现 MCP）
> 目标：使现成 MCP 服务连通鉴权并生产可用（见 `openspec/changes/mcp-auth/`）。

### Story 10.1 MCP 用户管理工具
- [ ] Task：`mcp_server.py` 新增 `auth_register` / `auth_login` / `auth_me`（api + db 双后端）

### Story 10.2 Token 透传与运维
- [ ] Task：`api` 后端 `_http` 支持 `AGENTBOARD_MCP_TOKEN` 注入 `Authorization`
- [ ] Task：启动脚本 `scripts/run_mcp.py` 或 README 写清 `python -m agentboard.mcp_server`
- [ ] Task：客户端配置样例 `examples/claude_desktop_mcp.json` / `examples/codebuddy_mcp.json`

### Story 10.3 验证与文档
- [ ] Task：新增 `tests/test_mcp_smoke.py`（FastMCP 客户端调用工具）
- [ ] Task：README「MCP 运行与接入」章节
- [ ] Task：更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单

---

## Epic 11：持续前端优化（模仿 Jira，小步迭代）【长期轨道】
> 目标：在不做破坏性重构、不擅自改动后端契约/数据模型的前提下，持续打磨 SPA，向 Jira 的交互密度与视觉语言靠拢。**每个自动任务周期只认领并交付一个 backlog 项。**
> 权威 backlog 与规则见 `openspec/changes/frontend-continuous/`（proposal/design/tasks）。

### 迭代规则（强制）
- **R1 单交付**：每周期只做 **一项**（A-xx）；做完即交付、即 commit，不囤积。
- **R2 范围红线**：单文件改动为主；新增前端代码 < ~80 行；不引入新 npm/打包依赖；不改 `models.py` / `api.py` 契约（除非该项标注「需后端」）。
- **R3 完成标准**：本地起 `api` + `web`，打开页面手测通过；若改动通用函数（`md()`/`api()` 等），跑一遍 `tests/test_web_flow.py` 与 Playwright 冒烟确认不回归。
- **R4 超限即拆**：某项偏大时拆回更细子项，本轮只做其一，剩余回写 backlog（保持 unchecked）。
- **R5 记录**：每完成一项，勾选本 Epic 对应项并追加「完成记录」（日期 + 一句话）；积累 5~8 项可写一份前端演化小结（非强制）。
- **commit 规范**：`feat(ui): 前端小优化 - <一句话描述>`。

### Backlog A（纯前端，可直接做，按推荐顺序）
- [x] **A-01 看板视图（Story 页）**：在 Story 详情增加「看板」视图，按 `META.statuses` 分列展示 task 卡片（只读，先无拖拽），复用 `/api/stories/{id}/tasks`。仅改 `app.js` + `style.css`。
- [x] **A-02 状态色徽章**：为各 status 设计配色（backlog 灰 / todo 蓝 / in_progress 黄 / in_review 紫 / verifying 橙 / done 绿），bug 红色调，参照 Jira。`style.css` 增加 `.badge.status--<status>` 类，`app.js` 的 `statusSelect`/列表渲染套用。
- [x] **A-03 任务类型图标**：task/bug 用内联 SVG 或 Unicode 图标替代纯文字徽章（如 ▢ / 🐞），不引入图标库。仅改 `app.js` + `style.css`。
- [x] **A-04 行内快速编辑标题**：双击列表项标题或详情标题进入编辑态，失焦/回车 PATCH 保存，Esc 取消。仅改 `app.js` + 少量 CSS。
- [x] **A-05 全局搜索框**：顶部加搜索输入，按 `title` 实时过滤当前页列表（纯前端过滤，不改 API）。
- [x] **A-06 状态流转按钮组**：将任务详情的「状态下拉 + 更新按钮」改为 Jira 式状态工作流按钮，点击即 `PUT /api/tasks/{id}/status`。
- [ ] **A-07 加载骨架屏**：将「加载中…」替换为轻量 spinner / 骨架占位，避免布局跳动。
- [ ] **A-08 空状态优化**：列表为空时显示引导文案/插画（如「暂无 Epic，点击新建」）替代灰色「暂无」。
- [ ] **A-09 进度条（Epic/Story）**：按子项 `status` 计算完成度（done 占比），在卡片显示进度条。
- [ ] **A-10 深色模式开关**：基于 CSS 变量切换明/暗主题，偏好存 `localStorage`。
- [ ] **A-11 响应式布局**：窄屏下树列表 / 两栏布局自动堆叠，按钮可点。
- [ ] **A-12 Toast 堆叠与动画**：多条 toast 不互相覆盖，进出场有过渡。
- [ ] **A-13 任务详情抽屉**：点击列表项从右侧滑出详情抽屉（含 description/spec/状态），不跳路由；关闭回列表。
- [ ] **A-14 Markdown 编辑工具栏**：description/spec 文本框上方加「加粗/列表/标题」快捷按钮，插入 markdown 语法。
- [ ] **A-15 键盘快捷键**：`j/k` 上下移动选中项、`e` 编辑、`Esc` 关闭弹层。
- [ ] **A-16 复制链接**：任务/Story 提供「复制深链」（如 `#/task/123`）按钮。
- [ ] **A-17 路由过渡动画**：视图切换加淡入/滑入过渡。
- [ ] **A-18 面包屑高亮当前级**：确保各级面包屑链接正确且高亮当前级（补样式）。
- [ ] **A-19 列表项 hover 操作**：hover 显示「编辑/删除」快捷图标，减少误触确认。
- [ ] **A-20 前端偏好本地存储**：记住上次视图（列表/看板）等前端偏好。

### Backlog B（需后端配合，先提需求，不混入小优化）
- [ ] **B-01 标签 / 标签组（labels）**：task 增加 `labels` 字段 + 多选 UI（需 `models.py` + 迁移 + API）。
- [ ] **B-02 负责人 / 指派（assignee）**：task 增加 `assignee` + 用户下拉（依赖 FR-8 用户体系）。
- [ ] **B-03 截止日期（due_date）**：task 增加 `due_date` + 日历控件 + 逾期高亮。
- [ ] **B-04 看板拖拽排序**：拖动卡片变更 status（需后端接受合法迁移 + 可选 order 字段）。
- [ ] **B-05 评论 / 活动流**：task 增加评论（需新表 + API）。
- [ ] **B-06 列表分组 / 排序**：按状态/类型/负责人分组（部分可纯前端，分组维度来自后端字段）。

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-10 | A-01 | Story 页只读看板（列表/看板切换），复用现有 tasks 接口与 statusBadge |
| 2026-07-10 | A-02 | 状态色徽章 Jira 风格：statusBadge 改用 `.badge.status--<status>` CSS 类，下拉选项与任务详情头部同步套用，bug 红色强调 |
| 2026-07-11 | A-03 | 任务类型图标：新增 `typeIcon()` 内联 SVG（task=勾选圆环 / bug=瓢虫），替换看板/列表/详情/徽章中的 emoji，不引入图标库 |
| 2026-07-11 | A-04 | 行内快速编辑标题：列表项(Epic/Story/Task)标题与 Task 详情标题支持双击进入编辑态，回车/失焦 PATCH 保存、Esc 取消；列表项用单击导航/双击编辑计时区分 |
| 2026-07-11 | A-05 | 全局搜索框：顶部栏加 `type=search` 输入，按标题实时过滤当前页列表（项目卡/实体列表/项目表/看板），空结果提示；查询词跨路由持久化，纯前端不改 API |
| 2026-07-11 | A-06 | 状态流转按钮组：任务详情用 Jira 式工作流按钮（当前状态药丸 + 合法迁移按钮）替换「下拉+更新按钮」，点击即 `PUT /api/tasks/{id}/status`；前端 `STATUS_TRANSITIONS` 镜像后端状态机，后端仍权威校验 |
