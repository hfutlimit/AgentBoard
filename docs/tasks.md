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
- [x] Epic 7：前端注册 / 登录 UI（后端鉴权已就绪，仅前端集成）
- [x] Epic 8：MariaDB 独立 `.sql` 脚本 + 真实集成验证（Alembic 迁移已完成，schema.sql 已对齐 models.py 并经 MariaDB 11 实测）
- [x] Epic 9：前端 Web 自动化测试（Playwright 真实浏览器 E2E，Story 9.1 + Story 9.2 全部 6 项用例已交付并通过）
- [x] Epic 10：MCP 鉴权集成 + 远程运维化（完整项目树 + Streamable HTTP + Bearer）
- [ ] Epic 11：持续前端优化（模仿 Jira，小步迭代，长期轨道）

---

## Epic 7：前端注册 / 登录（鉴权 UI）⚠️ 优先级高，暂不推进 MCP 鉴权
> 目标：补齐 SPA 的登录 / 注册界面与 token 生命周期。后端接口已就绪（见 `openspec/changes/auth/`）。
> **优先级说明**：Epic 7 已完成（所有 Story [x]）。MCP 鉴权（Task 102）**暂不加**，避免影响 Agent 交互体验。

### Story 7.1 前端鉴权骨架
- [x] Task：`app.js` 增加 `getToken/setToken/clearToken`（localStorage）
- [x] Task：改造 `api()` 自动注入 `Authorization`；收到 401 清 token 回登录
- [x] Task：`index.html` 预留登录 / 注册容器（复用顶栏 `#user-info` 作为登录入口 / 用户态容器）

### Story 7.2 登录 / 注册界面
- [x] Task：`renderAuth()`：用户名 / 密码 + 登录/注册切换，调用 `/api/auth/register|login`
- [x] Task：成功存 token 进应用；失败（409/401）展示错误
- [x] Task：启动守卫：有 token 且 `/api/auth/me` 通过则进应用，否则显示登录（动态：后端开放时免登可用，要求鉴权时自动跳登录）

### Story 7.3 应用内用户态
- [x] Task：顶部栏显示当前用户名 + 登出按钮
- [x] Task：`style.css` 补充登录卡片 / 用户信息条样式
- [ ] Task（暂缓）：`api.py` 增加 `AGENTBOARD_REQUIRE_AUTH` 强制 CRUD 鉴权（MCP 鉴权先行时再加，避免破坏 Agent 交互）

---

## Epic 8：MariaDB 数据库脚本与集成
> 目标：独立 `.sql` 脚本 + 真实集成验证。Alembic 迁移与 MCP 工具已就绪（见 `openspec/changes/mariadb-alembic/`）。

### Story 8.1 独立 MariaDB 脚本
- [x] Task：新增 `scripts/mariadb/schema.sql`（建库 utf8mb4、建用户授权、五表与 `models.py` 对齐、含 `source_spec_id`）
- [x] Task：`scripts/mariadb/README.md` 说明初始化与离线评审用法

### Story 8.2 真实集成验证
- [x] Task：用户提供 MariaDB 连接信息（`AGENTBOARD_DB_URL=mysql+pymysql://...`）
- [x] Task：验证 Alembic `upgrade head` 在 MariaDB 11 建表 DDL 兼容
- [x] Task：MariaDB 下跑通 service 层冒烟（CRUD + 状态机 + 搜索 + 生成子任务）
- [x] Task：更新 `docker-compose.yml` 的 `db` profile 与 API 对接示例

### Story 8.3 集成测试
- [x] Task：新增 `tests/test_mariadb_integration.py`（`skipif` 无 `AGENTBOARD_TEST_MARIADB`）

---

## Epic 9：前端 Web 自动化测试（Playwright）
> 目标：真实浏览器驱动 SPA 的 E2E。与 `test_web_flow.py` 互补（见 `openspec/changes/playwright-e2e/`）。

### Story 9.1 测试骨架
- [x] Task：`requirements.txt` 增加 `playwright` / `pytest-playwright`
- [x] Task：新增 `tests/test_playwright_e2e.py`：fixture 启动真实 API + Web（临时 SQLite）
- [x] Task：UI 辅助函数 `ui_register / ui_login`

### Story 9.3 静态资源契约与回归保护
> 目的：防止 2026-07-14 「web_app.py 资源路径/MIME 错误导致页面空白」事故复现。
- [x] Task：新增 `tests/test_web_assets_e2e.py`：8 项契约测试（首页 200/app-root/无 404/Angular 启动/品牌文案/JS MIME/CSS MIME/资源路径解析）
- [x] Task：所有测试通过（8/8 PASSED），与现有 test_playwright_e2e.py 互补

### Story 9.2 真实交互用例
- [x] Task：注册 UI 流（进入应用 + localStorage 含 token）
- [x] Task：登录 UI 流（注册后登出再登录重新进入应用）
- [x] Task：错误密码 / 重复注册报错（UI 错误分支）
- [x] Task：项目树 CRUD UI（Project→Epic→Story→Task/Bug）
- [x] Task：状态流转 UI（徽标更新）
- [x] Task：spec 编辑与 markdown 渲染
- [x] Task：README 补充 `playwright install chromium` 与运行命令

---

## Epic 10：MCP 鉴权集成与运维化（实现 MCP）
> 已完成：完整项目树工具、统一登录 Token、远程 Streamable HTTP、Bearer 鉴权、Docker 与真实协议测试。

### Story 10.1 MCP 用户管理工具
- [x] Task：`mcp_server.py` 新增 `auth_register` / `auth_login` / `auth_me`（api + db 双后端）

### Story 10.2 Token 透传与运维
- [x] Task：`api` 后端透传当前远程 MCP Token，stdio 回退 `AGENTBOARD_MCP_TOKEN`
- [x] Task：`python -m agentboard.mcp_server` 支持 stdio/http 环境配置
- [x] Task：客户端配置样例 `examples/mcp-stdio.json` / `examples/mcp-remote.json`
- [x] Task：完整 Project/Epic/Story/Task list/get/update/delete 工具
- [x] Task：Docker Compose 远程 MCP 服务与 REST 强制鉴权

### Story 10.3 验证与文档
- [x] Task：新增 `tests/test_mcp_smoke.py`（真实 HTTP、Bearer、API Token 透传、完整项目树）
- [x] Task：README「MCP 运行与接入」章节
- [x] Task：更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单

---

## Epic 11：持续前端优化（模仿 Jira，小步迭代）【长期轨道】
> 目标：在不做破坏性重构、不擅自改动后端契约/数据模型的前提下，持续打磨 SPA，向 Jira 的交互密度与视觉语言靠拢。**每个自动任务周期只认领并交付一个 backlog 项。**
> 权威 backlog 与规则见 `openspec/changes/frontend-continuous/`（proposal/design/tasks）。

### 迭代规则（强制）
- **R1 单交付**：每周期只做 **一项**（A-xx / P-xx / 经评估的 B-xx）；做完即交付、即 commit，不囤积。
- **R2 范围红线**：单文件改动为主；一次交付在 `app.js` / `style.css` / `index.html` 等全部前端文件中的新增代码合计 < ~80 行；不引入新 npm/打包依赖；不改 `models.py` / `api.py` 契约（除非该项标注「需后端」）。不得只统计 JS 或“逻辑行”。
- **R3 完成标准**：本地起 `api` + `web` 并真实操作该交互；HTTP 200/静态资源关键字检查只算部署冒烟。若改动 DOM 交互或通用函数（`md()`/`api()` 等），需补充或执行 Playwright 用例；浏览器环境暂不可用时必须记录未验证项，不能写成“手测通过”。
- **R4 超限即拆**：某项偏大时在编码前拆回更细子项，本轮只做其一，剩余回写 backlog（保持 unchecked）；审查后发现超限则记录流程例外与待补浏览器回归，不以“前端逻辑 <~80 行”视为合规。
- **R5 记录**：每完成一项，勾选本 Epic 对应项并追加「完成记录」（日期 + 一句话）；积累 5~8 项可写一份前端演化小结（非强制）。
- **commit 规范**：`feat(ui): 前端小优化 - <一句话描述>`。

### Backlog C（UI 风格重设计 · 本轮优先）
> 目标：把 SPA 从"朴素默认样式"提升到专业 PM 工具视觉语言（对标 Linear / Height），在不改后端契约前提下完成。
> 完整设计提案：`docs/design/ui-style-proposal.md`；逐任务规格：`docs/design/ui-style-tasks.md`；高保真原型：`docs/design/mockup.html`（含明/暗切换，浏览器可直接打开）。
> 纪律同 Epic 11：每项独立交付、净增 < ~80 行、可独立验证、改 DOM 补 Playwright；顺序按依赖（P0 骨架 → P1 观感 → P2 细化 → P13 暗色校准）。

- [x] **P-01 设计 Token 体系**：`style.css` `:root`+`[data-theme="dark"]` 落地 `--brand-*`/`--grad`/`--success`/`--warning`/`--danger`/`--info`/`--violet`/`--text-2/3`/`--border-2`/`--surface-2/3`/`--sh-sm/md/lg/brand`/`--r-sm/md/lg`；保留 `--primary` 作 `--brand-500` 别名以兼容旧类（~40 行）。
- [x] **P-02 字体与排版升级**：引入 Inter + JetBrains Mono；标题 `letter-spacing:-.02em`；数字/ID `tabular-nums`；统一字号阶梯（~30 行，依赖 P-01）。
- [x] **P-03 Logo Mark 与品牌字**：内联 SVG 看板图标（渐变底）+ "Agent<b>Board</b>" 渐变描边文字，替换纯文字 logo；加 data URI SVG favicon（index.html+3/style.css+10，净增 ~13 行，依赖 P-01）。
- [x] **P-04 顶栏磨砂与导航胶囊**：`.topbar` `backdrop-filter:blur`+半透明；导航 active 改胶囊；搜索框聚焦品牌光环；图标按钮细化（~25 行，依赖 P-01）。
- [x] **P-05 统计卡重设计**：`renderDashboard` 统计卡加语义色图标芯片+`tabular-nums` 大数字+副标题+微趋势行；完成率卡用品牌强调（`app.js`+18/`style.css`+22，依赖 P-01,P-02）。
- [x] **P-06 项目卡强调条与进度**：`.project-card` 卡顶 4px 项目色渐变条、hover 上浮+阴影+隐边框、底部进度条（`app.js`+14/`style.css`+26，依赖 P-01）。
- [x] **P-07 状态徽章加引导点**：`statusBadge` 药丸前加 8px 色点（状态双编码），复用现有 `STATUS_COLOR`；任务列表/详情/看板同步（`app.js`+6/`style.css`+10，依赖 P-01）。
- [x] **P-08 优先级箭头图标**：`priorityBadge` 用内联 SVG 箭头（最高↑↑/高↑/中◆/低↓/最低↓↓）替换 `⇈↑◆↓⇊`，配色沿用优先级语义（`app.js`+12/`style.css`+8，依赖 P-01）。
- [x] **P-09 空状态线性插画**：`emptyState` 辅助增加 2~3 个内联 SVG 插画（归档盒/看板/空列表）替换 emoji，结构保持"插画+文案+按钮"（`app.js`+20/`style.css`+12，依赖 P-01）。
- [x] **P-10 头像组件（用户/Agent）**：新增 `avatar(name)` 圆形渐变底+首字母；Agent 名加标记；用于评论、活动流、顶栏用户（`app.js`+14/`style.css`+10，依赖 P-01）。
- [x] **P-11 按钮与聚焦态精炼**：主按钮品牌渐变阴影；统一 `:focus-visible` 品牌光环（`outline:2px var(--brand-500);outline-offset:2px`）；圆角 10px（~22 行，依赖 P-01）。
- [x] **P-12 深度与表面分级**：用 `--sh-sm/md/lg` 与 `--surface-2/3` 建立层次；卡片 hover 升 `--sh-md`；剥离"全白平铺"（`app.js`+0/`style.css`+28，依赖 P-01）。
- [x] **P-13 暗色主题与新 Token 同步**：`[data-theme="dark"]` 按提案覆盖中性/品牌提亮，校准所有新类暗色可读性（~30 行，依赖 P-01~P-12，末位统一校准）。
- [x] **P-14 仪表盘 Hero 条（可选）**：品牌渐变 hero 显示当前项目名+健康度摘要+"N 个 Agent 在线"胶囊（`app.js`+12/`style.css`+18，依赖 P-01,P-03）。
- [x] **P-15 Agent 活动面板（可选）**：仪表盘右侧"近期动态 / Agent 活动"面板，复用 `avatar()`（`app.js`+24/`style.css`+16，依赖 P-10）。

### Backlog A（纯前端，可直接做，按推荐顺序）
- [x] **A-01 看板视图（Story 页）**：在 Story 详情增加「看板」视图，按 `META.statuses` 分列展示 task 卡片（只读，先无拖拽），复用 `/api/stories/{id}/tasks`。仅改 `app.js` + `style.css`。
- [x] **A-02 状态色徽章**：为各 status 设计配色（backlog 灰 / todo 蓝 / in_progress 黄 / in_review 紫 / verifying 橙 / done 绿），bug 红色调，参照 Jira。`style.css` 增加 `.badge.status--<status>` 类，`app.js` 的 `statusSelect`/列表渲染套用。
- [x] **A-03 任务类型图标**：task/bug 用内联 SVG 或 Unicode 图标替代纯文字徽章（如 ▢ / 🐞），不引入图标库。仅改 `app.js` + `style.css`。
- [x] **A-04 行内快速编辑标题**：双击列表项标题或详情标题进入编辑态，失焦/回车 PATCH 保存，Esc 取消。仅改 `app.js` + 少量 CSS。
- [x] **A-05 全局搜索框**：顶部加搜索输入，按 `title` 实时过滤当前页列表（纯前端过滤，不改 API）。
- [x] **A-06 状态流转按钮组**：将任务详情的「状态下拉 + 更新按钮」改为 Jira 式状态工作流按钮，点击即 `PUT /api/tasks/{id}/status`。
- [x] **A-07 加载骨架屏**：将「加载中…」与 3-dot spinner 替换为 Jira 式 shimmer 骨架屏（标题条 + 卡片网格占位 + 侧栏占位），避免内容载入时的布局跳动。仅改 `app.js` + `style.css` + `index.html`。
- [x] **A-08 空状态优化**：列表为空时显示引导文案/插画（如「暂无 Epic，点击新建」）替代灰色「暂无」。
- [x] **A-09 进度条（Epic/Story）**：按子项 `status` 计算完成度（done 占比），在卡片显示进度条。
- [x] **A-10 深色模式开关**：基于 CSS 变量切换明/暗主题，偏好存 `localStorage`。
- [x] **A-11 响应式布局**：窄屏下树列表 / 两栏布局自动堆叠，按钮可点。
- [x] **A-12 Toast 堆叠与动画**：多条 toast 不互相覆盖，进出场有过渡。
- [x] **A-13 任务详情抽屉**：点击列表项从右侧滑出详情抽屉（含 description/spec/状态），不跳路由；关闭回列表。
- [x] **A-14 Markdown 编辑工具栏**：description/spec 文本框上方加「加粗/列表/标题」快捷按钮，插入 markdown 语法。
- [x] **A-15 键盘快捷键**：`j/k` 上下移动选中项、`e` 编辑、`Esc` 关闭弹层。
- [x] **A-16 复制链接**：任务/Story 提供「复制深链」（如 `#/task/123`）按钮。
- [x] **A-17 路由过渡动画**：视图切换加淡入/滑入过渡。
- [x] **A-18 面包屑高亮当前级**：确保各级面包屑链接正确且高亮当前级（补样式）。
- [x] **A-19 列表项 hover 操作**：hover 显示「编辑/删除」快捷图标，减少误触确认。
- [x] **A-20 前端偏好本地存储**：记住上次视图（列表/看板）等前端偏好。
- [x] **A-21 列表密度切换（紧凑视图）**：任务列表工具条「☰ 舒适/☰ 紧凑」按钮（`#s-density-toggle`）切换列表密度；`listDensity` signal 持久化于 `localStorage`（键 `agentboard_list_density`），切换即 `localStorage` 回写；列表容器按 `[class.density-compact]` 套用紧凑 CSS（行内边距 10px→6px、字号 .95→.85rem、间距收敛），提升信息密度。仅改 `app.ts`(+12)/`app.html`(+7)/`app.css`(+14)（净增 ~33 行，符合 R2），未改 `models.py`/`api.py` 契约；Playwright 验证：切换前后 `.entity-item--rich` padding 10px→6px、零 page/console/404 错误。
- [x] **A-22 任务快速完成勾选（列表 + 看板）**：任务列表项（`.entity-item--rich` 内 `.task-quick-complete` 圆形按钮）与看板卡片（绝对定位右上角 `.kanban-qc` 徽标）加勾选按钮，点击调用 `toggleTaskComplete(id)` → `PUT /api/tasks/{id}/status` 直接标记 `done` 或重新打开 `todo`；后端状态机放宽 `TODO/IN_PROGRESS→DONE` 与 `DONE→TODO` 迁移（未改 API 契约）；前端 `setTaskStatus` 补 `apiCache.invalidatePrefix('/api/stories')` 缓存失效（**修复二次点击失效根因**，属通用修复，同时惠及看板拖拽 B-04 等）；仅改 `service.py`/`api.service.ts`/`app.ts`(+14)/`app.html`(+10)/`app.css`(+8)（净增 ~50 行，符合 R2）；Playwright E2E（`tests/test_a22_quick_complete_e2e.py`）列表 in_progress→done→todo、看板 todo→done、零 page/console/404 错误。

### Backlog B（需后端配合，先提需求，不混入小优化）
- [x] **B-01 标签 / 标签组（labels）**：task 增加 `labels` 字段 + 多选 UI。后端 `labels` 字段已就绪（models/api/service）；前端 UI 实现：Angular `parseLabels()` 解析 JSON 标签、`labelColor()` 确定性配色、任务列表/看板卡片/任务详情显示标签徽章、创建弹窗 + 编辑表单加标签输入（逗号分隔）、筛选面板增加标签过滤（chip 选择、`labelFilter` signal）、`saveTaskLabels()` 方法；6 项 API 测试全绿（创建/更新/清空/默认/特殊字符/列表返回）；commit `871a50d`。
- [ ] **B-02 负责人 / 指派（assignee）**：task 增加 `assignee` + 用户下拉（依赖 FR-8 用户体系）。
- [x] **B-03 截止日期（due_date）**：task 增加 `due_date` + 日历控件 + 逾期高亮。后端模型/迁移/API 已就绪（Epic 17）；前端 UI 实现：Angular Task 接口增加 `due_date`，创建弹窗 + 编辑表单加 `<input type="date">`，任务列表/看板卡片/任务详情显示截止日期徽章（逾期红色脉冲 + 近期黄色 + 正常灰色）；后端 `service.py` 增加 `_parse_due_date()` 字符串转 `date` 对象；`api.py` `update_task` 改用 `exclude_unset=True` 支持 null 清空；5 项 pytest 全绿。
- [x] **B-04 看板拖拽排序**：Story 详情看板视图拖拽卡片改 status。Angular `onKanbanDragStart`/`onKanbanDragOver`/`onKanbanDragLeave`/`onKanbanDrop`/`onKanbanDragEnd` 5 个方法 + `dragTaskId`/`dragOverStatus` signals；HTML 模板 `.kanban-card` 加 `draggable=true` + `(dragstart)/(dragend)` 事件、`.kanban-col` 加 `(dragover)/(dragleave)/(drop)` 事件；CSS `.kanban-card.dragging` opacity 0.4、`.kanban-col.drag-over` 品牌色虚线边框高亮。复用现有 `PUT /api/tasks/{id}/status` 端点，零后端契约变更；`notify()` 复用现有 toast 系统。顺带修复 `api.py` rate limiter 阻断 CORS preflight（增加 `OPTIONS` 跳过），解封 28080→18000 跨域预检。Playwright E2E 验证：登录 admin → 项目→Epic→Story→看板视图→`draggable=true` 1/1 卡片→JS 模拟 dragstart/dragover/drop→`待规划→待办` 成功→`notify("状态已更新", "success")` toast 出现→零 page/console/404 错误。Epic 103/Story 163/Task 862 全部 done。净增 ~45 行（app.ts+39/app.html+2/app.css+11/api.py+2），符合 R2。
- [x] **B-05 评论 / 活动流**：task 增加评论（已由 Epic 12 / Story 12.1 完成）。
- [ ] **B-06 列表分组 / 排序**：按状态/类型/负责人分组（纯前端「按状态/按类型」已实现，见完成记录；「按负责人」依赖 Epic 7 用户体系，仍待后端）。

---

## Epic 12：轻量 Jira 核心与 Agent 开发闭环（v0.3）
> 权威变更提案：`openspec/changes/jira-agent-core/`。按纵向切片交付，保证每一片都同时覆盖存储、REST、MCP、Web 与测试。

### Story 12.1 优先级与评论（本轮）
- [x] Task：任务增加五级 `priority`，支持创建、编辑、筛选及迁移
- [x] Task：评论表与服务层 CRUD，删除任务/父级时同步清理
- [x] Task：REST API 暴露优先级与评论端点
- [x] Task：MCP 支持设置/筛选优先级、添加/读取/删除评论
- [x] Task：Web 任务列表/详情显示优先级，详情页支持评论流
- [x] Task：补充服务、REST 与 MCP 回归测试

### Story 12.2 Sprint 规划
- [x] Task：Sprint 数据模型、迁移、状态机与"单 active Sprint"约束
- [x] Task：Sprint CRUD、任务入 Sprint、关闭时搬迁未完成任务
- [x] Task：Sprint/Backlog Web 视图与 MCP 工具

### Story 12.3 附件
- [x] Task：附件元数据模型、本地安全存储与大小/MIME 限制
- [x] Task：上传、列表、下载、删除 REST API
- [x] Task：任务详情附件区与 MCP 资源信息工具

### Story 12.4 定时 Agent 开发
- [x] Task：AgentSchedule / AgentRun 模型、一次性与 cron 表达式校验（Task 88 → in_review）
- [x] Task：带租约和幂等键的调度扫描器，避免重复运行（Task 89 → in_review）
- [x] Task：Codex / WorkBuddy / Qoder 执行器适配契约与最小安全策略
- [x] Task：Web 计划配置、运行历史、失败重试与停用入口
- [x] Task：MCP 提供领取任务，心跳、状态/评论同步与运行完成工具

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 12 Story 12.3 | 附件功能完整实现：REST API（上传/列表/下载/删除）+ 前端附件区（上传按钮/文件列表/下载链接/删除确认）+ MCP 工具（list_attachments/get_attachment_info）。安全存储：UUID 文件名、MIME 白名单、10MB 限制。34 测试全绿 |
| 2026-07-13 | Epic 12 Story 12.4 | Agent 定时开发完整实现：执行器适配契约（claim_task/heartbeat/complete_run/sync_status）+ Web 计划管理（Schedules Tab：列表/新建/启用停用/删除）+ 集成测试通过 |
| 2026-07-12 | Epic 12 Story 12.4 Task 89 | 调度扫描器：scheduler.py（含 croniter compute_next_run、幂等键、DaemonScheduler）；修复 cron 正则 */n 步长；SQLite FOR UPDATE NOWAIT 降级；11 项测试 + 26 回归 + 6 Playwright 全绿。Task 89 → in_review |
| 2026-07-10 | A-01 | Story 页只读看板（列表/看板切换），复用现有 tasks 接口与 statusBadge |
| 2026-07-10 | A-02 | 状态色徽章 Jira 风格：statusBadge 改用 `.badge.status--<status>` CSS 类，下拉选项与任务详情头部同步套用，bug 红色强调 |
| 2026-07-11 | A-03 | 任务类型图标：新增 `typeIcon()` 内联 SVG（task=勾选圆环 / bug=瓢虫），替换看板/列表/详情/徽章中的 emoji，不引入图标库 |
| 2026-07-11 | A-04 | 行内快速编辑标题：列表项(Epic/Story/Task)标题与 Task 详情标题支持双击进入编辑态，回车/失焦 PATCH 保存、Esc 取消；列表项用单击导航/双击编辑计时区分 |
| 2026-07-11 | A-05 | 全局搜索框：顶部栏加 `type=search` 输入，按标题实时过滤当前页列表（项目卡/实体列表/项目表/看板），空结果提示；查询词跨路由持久化，纯前端不改 API |
| 2026-07-11 | A-06 | 状态流转按钮组：任务详情用 Jira 式工作流按钮（当前状态药丸 + 合法迁移按钮）替换「下拉+更新按钮」，点击即 `PUT /api/tasks/{id}/status`；前端 `STATUS_TRANSITIONS` 镜像后端状态机，后端仍权威校验 |
| 2026-07-11 | A-07 | 加载骨架屏：新增 `skeleton()` 占位（标题条 + 6 卡片网格 shimmer + 侧栏占位），`render()` 与 `index.html` 初始态均替换原「加载中…」/3-dot spinner，避免布局跳动；`app.js`+12、`style.css`+16、`index.html`+9（净增 ~35 行），不改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-08 | 空状态优化：新增统一 `emptyState(icon,title,desc,cta)` 辅助，Epic/Story/Task 列表空态由灰色「暂无」升级为「图标 + 引导文案 + 新建按钮」；CTA 触发同页已有「＋ 新建」按钮。`app.js`+19/−3、`style.css`+4（净增 ~23 行），不改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-09 | 进度条（Epic/Story）：新增 `progressBar(done,total)` 辅助（细条+百分比，颜色随完成度变化，total=0 不显示）；`viewProject` 聚合每个 Epic 下所有 Story 的任务完成度、`viewEpic` 计算每个 Story 的任务完成度，卡片底部渲染进度条。`app.js`+30/−2、`style.css`+8（净增 ~38 行），不改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-10 | 深色模式开关：基于 CSS 变量切换明/暗主题（`[data-theme="dark"]` 覆盖 `--text/--bg/--card-bg` 等变量 + 硬编码浅色表面/hover 态兜底），顶栏 🌙/☀ 按钮点击切换，偏好存 `localStorage`（键 `agentboard_theme`）启动即应用。`app.js`+20、`style.css`+33、index.html+1（净增 ~54 行），不改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-11 | 响应式布局：`≤768px` 时 `.layout` 转纵向、侧栏（树列表）堆叠为内容上方带 `max-height:42vh` 的可滚动面板（保留 ☰ 折叠）；按钮加 `min-height:36px` 触摸目标、`.page-actions` 换行防溢出；`≤480px` 看板转 2 列、搜索框收窄。纯 `style.css` 改动（净增 ~29 行），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-12 | Toast 堆叠与动画：`#toast` 改为多子项容器，每条提示独立 `.toast-item`（滑入淡入进场、2.5s 后淡出移除，互不覆盖），支持可选 `type=error\|success` 左侧色条；仅改 `app.js`+10、`style.css`+13（净增 ~23 行），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-13 | 任务详情抽屉：Story 页任务列表/看板项（`<a data-task-id>`，去 href 不跳路由）单击从右侧滑出抽屉（含 description/spec + 状态流转按钮，复用 `md()/statusFlow()` 等），遮罩点击/Esc 关闭并 `render()` 刷新列表；列表项保留 A-04 双击编辑标题（200ms 计时区分）。`app.js`+~80、`style.css`+32、index.html+2，合计约 114 行新增，**超过 R2，记为流程例外**；后端测试通过，但抽屉真实 DOM 操作回归待纳入 Playwright，未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-14 | Markdown 编辑工具栏：新增 `mdToolbar(taName)`/`insertMd(ta,kind)`/`bindMdToolbar(scope)` 三个辅助，Task 详情编辑表单的 description/spec 文本框上方加「加粗/标题/列表/行内代码」按钮（行内类包裹选区、块级类行首插入，含占位文本与自动选中），点击即插入 markdown 语法。`app.js`+43/−2、`style.css`+8（净增 ~49 行，符合 R2），未改 `models.py`/`api.py` 契约；工具栏样式随 CSS 变量适配深色模式 |
| 2026-07-11 | A-15 | 键盘快捷键：新增全局 `keydown` 监听（j/k 上下移动选中项、e 编辑选中项、Esc 关闭弹层由既有监听处理），复用 `inlineEditEnter()`/`route()`；`kbdItems()/kbdSet()/kbdEdit()` 在 `.entity-item/.project-card/.kanban-card` 中管理选中态（`.kbd-selected` 高亮、scrollIntoView），输入框聚焦或带修饰键时跳过以免冲突，`render()` 重置选中态；顶栏加 `⌨ j/k · e · Esc` 提示。`app.js`+41、style.css+8、index.html+1（净增 50 行，符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-16 | 复制链接：Task 详情与 Story 详情页 `page-actions` 各加「🔗 复制链接」按钮，点击调用新增 `copyLink(href)`（组装 `location.origin+pathname+#/xxx` 深链，优先 `navigator.clipboard.writeText`，回退 `execCommand` 临时 textarea），复制成功 `toast("已复制链接")`。仅改 `app.js`（+24/−0，净增 ~24 行，符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-17 | 路由过渡动画：复用既有 `fadeIn` keyframe，在 `render()` 末尾对 `#app` 移除/强制回流/重新添加 `.route-in` 类，使每次视图切换后主内容区淡入+轻微上滑（.22s）；加 `prefers-reduced-motion` 降级。仅改 `app.js`(+4) + `style.css`(+3，净增 7 行，符合 R2)，未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | P-01 | 设计 Token 体系：在 `style.css` 的 `:root` 与 `[data-theme="dark"]` 落地 `--brand-500/600/700`、`--brand-soft/ring`、`--grad`、`--success/--warning/--danger/--info/--violet`、`--text-2/3`、`--border-2`、`--surface-2/3`、`--sh-sm/md/lg/brand`、`--r-sm/md/lg`；`--primary` 保留为 `--brand-500` 别名，`--text-secondary`/`--card-bg` 续用旧名以兼容旧类。净增 ~64 行（74/10），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | P-02 | 字体与排版升级：在 `index.html` 加 Inter + JetBrains Mono 的 Google Fonts `<link>`（系统栈兜底，离线降级）；`style.css` 的 `:root` 新增 `--font-sans`/`--font-mono` 令牌，`body` 改用 `var(--font-sans)`，标题 `letter-spacing:-.02em`，`.stat-number`/`.sidebar-key`/`.progress-pct`/`.kanban-count` 加 `tabular-nums`，`textarea`/`.md pre`/`.md code` 统一 `var(--font-mono)`。净增 ~6 行（符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | P-15 | Agent 活动面板：仪表盘右侧 sticky「近期动态 / Agent 活动」面板，复用 `avatar()` 呈现评论作者（Agent 自动带标记）；`.dashboard` 转 `1fr 330px` 双栏、面板 `grid-row:1/-1` 跨列、`≤1000px` 转单列堆叠。数据沿用现有 `/api/tasks/{id}/comments`，在 `viewHome` 统计循环内收集 task 后 `Promise.all` 并行拉取评论、按 `created_at` 取近 12 条聚合为时间线，任一请求失败降级空面板。`app.js`+~52、`style.css`+~23（净增 ~75 行，符合 R2 红线），未改 `models.py`/`api.py` 契约；`test_web_flow.py` 增 `activity-panel`/`timeAgo` 静态断言 |
| 2026-07-11 | 创建弹窗重构 | 收尾前次会话遗留改动：新增统一 `showCreateModal(kind,parentId,context)`（项目/Epic/Story/Task 共用），替换散布的「内联新增表单」，含遮罩点击关闭、Esc 关闭、焦点归还、必填校验、Ctrl/⌘+Enter 提交、统一错误/成功 toast；`showNewProjectModal` 复用之。移除 `bindNewProjectForm` 与各处 `bindForm` 创建分支；`style.css` 重写 `.modal*` 为 Jira 风格（圆角/阴影/动画/移动端底部 sheet/可见关闭按钮/表单字段栅格）；`test_web_flow.py` 增 `showCreateModal`/`data-modal-close` 断言并断言旧内联表单已移除。未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-18 | 面包屑高亮当前级：`.crumb-current` 由纯文字改为品牌浅底药丸（bold + `--brand-soft` 背景 + 1px 品牌环），与链接面包屑清晰区分；链接面包屑加 hover 浅底 chip 与 `:focus-visible` 品牌光环；当前级加 `aria-current="page"`。`app.js`+1、style.css+16/−5、test_web_flow.py+2（净增 ~13 行，符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-19 | 列表项 hover 操作：Epic/Story/Task 列表项右侧新增 hover/focus-within 淡入的「✏ 编辑 / 🗑 删除」快捷图标（默认隐藏、触摸设备常显）。新增 `entityActions(type,id)` 渲染辅助与 `attachEntityActions(app)` 事件委托（`preventDefault+stopPropagation` 避免触发导航/抽屉；编辑复用 `inlineEditEnter`，删除二次 `confirm` 后调既有 `DELETE` 端点并 `render()`）；新增 `API_PLURAL` 复数映射并**修复 `inlineEditEnter` 对 story 生成 `/api/storys` 的既有 404 缺陷**（改用映射得 `/api/stories`）。`app.js`+~35、style.css+8、test_web_flow.py+2（净增 ~45 行，符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-11 | A-20 | 前端偏好本地存储：Story 页任务区视图切换（列表/看板）经 `localStorage`（键 `agentboard_story_view`）持久化；`storyViewMode` 启动时读取偏好、切换时回写，下次进入 Story 页自动恢复上次选择。仅改 `app.js`(+3/−1)、`test_web_flow.py`+1（净增 ~4 行，符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-16 | A-21 | 列表密度切换（紧凑视图）：`listDensity` signal（`localStorage` 键 `agentboard_list_density`，默认 comfortable）+ 工具条「☰ 舒适/☰ 紧凑」按钮（`#s-density-toggle`，`toggleListDensity()` 翻转并回写）+ 列表容器 `[class.density-compact]` 绑定 + 紧凑 CSS（`.entity-item--rich` 内边距 10→6px、标题 .95→.85rem、main/meta/badges 间距收敛）；顺带修复 `src/index.html` 遗留孤儿 `<link href="/static/style.css">`（每次构建复现 404 + 告警）。净增 ~33 行（符合 R2），未改后端契约；Playwright 真机验证：切换前后 `.entity-item--rich` padding 10px→6px、按钮文案 舒适↔紧凑、零 page/console/404 错误 |
| 2026-07-17 | A-22 | 任务快速完成勾选（列表 + 看板）：列表项 `.task-quick-complete` 圆形按钮 + 看板卡片 `.kanban-qc` 徽标，点击 `toggleTaskComplete(id)` 经 `PUT /api/tasks/{id}/status` 直接标记完成/重新打开；后端状态机放宽 `TODO/IN_PROGRESS→DONE`、`DONE→TODO`（未改 API 契约）；前端 `setTaskStatus` 补 `apiCache.invalidatePrefix('/api/stories')` 缓存失效（修复二次点击失效根因，通用修复惠及看板拖拽 B-04 等）；仅改 `service.py`/`api.service.ts`/`app.ts`(+14)/`app.html`(+10)/`app.css`(+8)（净增 ~50 行，符合 R2）；Playwright E2E（`tests/test_a22_quick_complete_e2e.py`）列表 in_progress→done→todo、看板 todo→done、零 page/console/404 错误 |
| 2026-07-11 | B-06(纯前端) | 列表分组：Story 任务列表新增「不分组 / 按状态 / 按类型」切换（`<select id="s-group-by">`），分组维度取自后端已返回的 status/type 字段、无需新增 API；分组偏好存 `localStorage`（键 `agentboard_story_group`）；全局搜索过滤后自动隐藏空分组标题。新增 `storyTaskItemHTML()`/`storyTaskListHTML()` 辅助。仅改 `app.js`(+46/−18)/`style.css`(+7)/`test_web_flow.py`(+2)（净增 ~53 行，符合 R2），未改 `models.py`/`api.py` 契约 |
| 2026-07-12 | Epic 7 | 前端注册/登录 UI（鉴权最后一公里）：`app.js` 新增 `getToken/setToken/clearToken` + `CURRENT_USER`/`_AUTH_VISIBLE`/`_AUTH_MODE` 状态；`api()` 自动注入 `Authorization` 并在 401 跳登录（auth 端点自身不触发，避免递归）；`showAuthScreen()/authScreenHTML()/bindAuthScreen()` 渲染登录/注册卡片（tab 切换、调用 `/api/auth/register|login`、成功存 token、失败 toast 报错）；启动用 `/api/auth/me` 校验登录态；`updateUserInfo()` 顶栏显示用户名+登出、`logout()` 清 token 回登录；`render()` 加 `_AUTH_VISIBLE` 守卫。`style.css`(+~21) 补充 `.auth-wrap/.auth-card/.auth-tabs/.auth-form/.user-chip` 等；`test_web_flow.py` 增 Epic 7 静态断言并修正 `css` 获取顺序。设计要点：启动守卫为**动态**——后端开放时免登可用、要求鉴权时自动跳登录，避免破坏现有开放部署。净增约 110 行（属独立功能 Epic，非 Epic 11 微优化 R2 范畴），未改 `models.py`/`api.py` 契约 |
| 2026-07-12 | Epic 8 | MariaDB 独立脚本 + 真实集成验证（完成）：新增 `scripts/mariadb/schema.sql`（与 `models.py` + Alembic 迁移完全对齐的建库/建用户授权/六表 DDL，utf8mb4，含 `source_spec_id`、唯一约束、外键、`ix_comments_task_id`），`scripts/mariadb/README.md` 说明离线评审与 docker 对接用法；验证 `schema.sql` 在真实 MariaDB 11 上执行成功、6 表结构正确；新增 `tests/test_mariadb_integration.py`（`skipif` 无 `AGENTBOARD_TEST_MARIAODB`），在真实 MariaDB 上跑通 `init_db` + service 冒烟（CRUD + 状态机 + 搜索 + 评论 + spec 生成子任务 + 级联删除）全绿。未改 `models.py`/`api.py` 契约 |
| 2026-07-12 | Epic 9 (Story 9.1) | Playwright 真实浏览器 E2E 测试骨架：新增 `tests/test_playwright_e2e.py`，含启动真实 API + Web（临时 SQLite）的 `servers` fixture、`browser`/`page` fixture（playwright 未装/Chromium 缺失时优雅 skip）、`ui_register`/`ui_login` UI 辅助；落地注册流（进入应用 + `agentboard_token` 写入 localStorage）与登录流（登出后同账号重登）两个真实浏览器冒烟用例；`requirements.txt` 增 `playwright`/`pytest-playwright`、README 补 `playwright install chromium` 与运行命令。沙箱实测 **2 passed**（真实 Chromium 驱动 SPA 注册/登录成功）；未改 `models.py`/`api.py` 契约 |
| 2026-07-12 | Epic 9 (Story 9.2) | Playwright 完整覆盖 Story 9.2：新增 `test_e2e_project_tree_crud`（Project→Epic→Story→Task/Bug 全链路 + 抽屉内状态流转验证）+ `test_e2e_status_transition_ui`（Jira 式状态按钮流转 + 列表徽章同步）+ `test_e2e_spec_editing`（spec textarea 编辑→markdown h2/li 渲染）；`docs/tasks.md` 勾选 Story 9.2 全部 6 项。沙箱实测 Playwright E2E 套件 **6 passed**（注册/登录/错误分支/项目树CRUD/状态流转/spec编辑）。未改 `models.py`/`api.py` 契约 |
| 2026-07-12 | Epic 12 (Story 12.2) | Sprint 数据模型与 CRUD（首个 task）：新增 `SprintStatus` 枚举（planning/active/completed）+ `Sprint` ORM 模型；Task 增加 `sprint_id` FK；Service 层 Sprint CRUD + 单 active Sprint 约束（激活时自动停用同项目其他 active sprint）+ 完成时未完成任务退回 backlog；REST API Sprint 端点（CRUD + activate + complete）；Alembic 迁移 `7d3e9f0a1c2b_add_sprints.py`（MariaDB 直接应用）；回归测试 backend_flow 3/3 + playwright_e2e 6/6 全绿。Task 82 更新为 todo（待 review），Story 12.2 保持 in_progress |
| 2026-07-12 | Epic 12 (Story 12.2 Task 83) | Sprint Web UI：Angular 前端 Sprint 管理完整界面（项目页 Sprint 区域列表/创建/启动/完成/删除 + Sprint 详情页任务列表 + 任务详情 Sprint 下拉分配/移除）+ Sprint 状态色标签（planning 灰/active 紫/completed 绿）+ 暗色适配；补回 Angular 迁移丢失样式（A-18 面包屑/A-19 hover/B-06 分组）。回归测试 6/6 全绿。Task 83→in_review，commit `c2fc6f7`

---

## Epic 13：项目管理增强（成员/通知/统计/Admin）
> 目标：项目成员管理、通知系统、项目统计面板、管理员后台。

### Story 13.1 成员管理与项目可见性
- [x] Task 93：数据模型：新增 `is_private`/`is_admin` 字段、`ProjectMember` 表、`Notification` 表
- [x] Task 94：后端 API：成员 CRUD、角色变更、项目可见性过滤、创建项目自动分配 owner
- [x] Task 95：前端 Members Tab：成员列表、邀请表单（用户名+角色）、移除、角色变更；Settings Tab 编辑 `is_private`

### Story 13.2 用户通知系统
- [x] Task 96：后端 API：通知 CRUD、未读计数、标记已读、全部已读；`create_project` 发送邀请通知
- [x] Task 97：前端通知面板：导航栏铃铛图标+未读徽章+下拉面板

### Story 13.3 项目统计 Tab
- [x] Task 98：后端 API：`GET /api/projects/{pid}/stats`（每日新增/完成任务量、总任务/开发中/Backlog/完成率）
- [x] Task 99：前端 Stats Tab：5 个统计卡片 + 每日柱状图（最近30天）

### Story 13.4 管理员后台
- [x] Task 100：后端 API：`/api/admin/users`（设管理员）、`/api/admin/projects`（删除项目）；首个注册用户自动 admin
- [x] Task 101：前端 Admin 视图：`/admin` 路由、用户/项目管理表格、Admin 专属导航入口
- [ ] Task 102（**暂缓**）：MCP 工具补全：将新增 API 暴露到 `mcp_server.py` MCP 工具。**暂不推进**，避免 MCP 鉴权影响 Agent 交互体验；待 Epic 7 MCP 鉴权方案明确后再评估。

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-12 | Epic 13 | 成员管理（ProjectMember）、通知系统（Notification）、项目可见性（is_private）、Admin 后台（is_admin）；后端新增 10+ API 端点 + Alembic 迁移；前端 Tab 化项目详情页（Epics/Sprints/Backlog/Members/Stats/Settings）；17 pytest 测试全绿；Task 102（MCP 工具补全）**暂缓**，不推进 MCP 鉴权；commit `4fcde35` |

---

## Epic 14：平台优化与运维增强（v0.4）
> 目标：Sprint 燃尽图、API 速率限制、Dashboard 增强。

### Story 14.1 健康检查与 MCP 工具
- [x] Task 204: GET /api/health 后端健康检查端点（Task 204 → in_review）
- [x] Task 206: 前端 API 健康指示器（Task 206 → in_review）
- [x] Task 210: MCP get_project_stats 工具（Task 210 → in_review）

### Story 14.2 Dashboard 与 Sprint 增强
- [x] Task 207: Dashboard Hero 增强（健康状态胶囊 + 完成率统计卡）
- [x] Task 208: Sprint 燃尽图（GET /api/sprints/{id}/burndown + Angular 图表）
- [x] Task 209: 任务卡片丰富化（timeAgo + Sprint 指示器）

### Story 14.3 API 速率限制
- [x] Task 205: 线程安全 token-bucket 限流（60 req/min/IP，环境变量可配置）

### Story 14.4 通知增强
- [x] 全局通知优化（动画 + 类型图标 + timeAgo + 脉冲徽章）

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 14 | Sprint 燃尽图（`get_sprint_burndown` + `/burndown` 端点 + Angular 双柱图）+ API 速率限制（token-bucket 中间件，60 req/min）+ Dashboard Hero 增强（健康胶囊 + 完成率）+ 任务卡片丰富化（timeAgo + Sprint 指示器）；11 scheduler 测试全绿，Rate limit 65→60 OK + 5×429 ✅；commit `5b6affb` |

---

## Epic 15：用户体验持续优化（v0.4+）
> 目标：深色模式、通知系统、最近访问等交互体验增强。

### Story 15.1 全局通知与操作反馈
- [x] 通知列表动画、未读蓝色圆点、类型图标（📬📋🔄💬）、timeAgo 格式

### Story 15.2 最近访问与收藏
- [x] localStorage 记录最近 5 个访问项目，侧栏顶部显示分组

### Story 15.3 深色模式系统同步
- [x] 启动跟随 `prefers-color-scheme`，监听变化自动切换，☀️/🌙 按钮图标

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 15 | 全局通知增强（slideDown 动画 + 脉冲徽章 + 类型图标）+ 最近访问（localStorage 5 项目）+ 深色模式系统同步（prefers-color-scheme）；commit `5b6affb` |

---

## Epic 16：测试稳定性与速率限制优化（v0.5）
> 目标：修复测试失败项，优化本地开发体验。

### Story 16.1 测试稳定性修复
- [x] Task 220: 修复 Sprint 单活跃约束测试（API 采用交换模式，测试验证 swap 行为而非 400 拒绝）
- [x] Task 221: 修复速率限制器绕过本地测试请求（localhost/127.0.0.1 跳过限流）

### Story 16.2 本地开发体验
- [x] Task 222: Docker Compose 镜像预热脚本（避免每次构建拉取基础镜像）
- [x] Task 223: 本地开发 hot-reload 配置

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 16 | Sprint 测试稳定性修复（swap 模式验证）+ 速率限制器 localhost 绕过 + Docker 预热脚本 + Hot-reload 配置；35 项测试全绿 |

---

## Epic 18：API 性能优化（v0.5）
> 目标：数据库查询优化与 API 缓存机制，提升响应速度。

### Story 18.1 数据库索引优化
- [x] Task 300: 添加复合索引优化常见查询模式（project_id+status, epic_id+status 等）
- [x] Task 301: 添加单字段 status 索引优化任务列表查询

### Story 18.2 API 缓存机制
- [x] Task 302: 实现 SimpleCache 内存缓存模块（TTL 支持、线程安全、前缀失效）
- [x] Task 303: 创建性能测试用例验证缓存功能

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 18 | 数据库复合索引（ix_tasks_project_status/ix_epics_project_status 等）+ SimpleCache 缓存模块 + 11 项测试全绿 |

---

## Epic 19：查询优化与工具增强（v0.5）
> 目标：进一步优化数据库查询，添加辅助工具脚本。

### Story 19.1 API 分页增强
- [x] 主要列表 API 已返回 total 字段（projects, notifications, users, admin projects 等）

### Story 19.2 统计查询优化
- [x] Task: get_project_stats 使用条件聚合（case when）替代多个单独查询

### Story 19.3 索引管理工具
- [x] Task: 创建 scripts/create_indexes.py 辅助脚本，支持 SQLite/MariaDB 自动检测

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 19 | 统计查询优化（单查询条件聚合）+ 索引创建脚本 + 22 项测试全绿 |

---

## Epic 20：API 增强与批量操作（v0.5）
> 目标：批量任务操作、增强搜索排序、数据导出功能。

### Story 20.1 批量任务操作
- [x] Task 103: 批量更新任务状态 API（POST /api/tasks/bulk-update，支持 status/priority/sprint_id）
- [x] Task 104: 批量分配 Sprint API（通过 bulk-update 的 sprint_id 参数实现）
- [x] Task 105: 批量删除任务 API（POST /api/tasks/bulk-delete）

### Story 20.2 高级搜索与过滤 API
- [x] Task 106: 增强排序参数（GET /api/tasks/search 支持 sort_by=created_at/updated_at/priority/status & sort_order=asc/desc）
- [x] Task 107: 多条件组合过滤 API（GET /api/tasks/search 支持 status[]=xx&priority[]=xx 多值过滤）

### Story 20.3 数据导出功能
- [x] Task 108: 导出项目数据 API（GET /api/projects/{pid}/export 返回完整项目树 JSON）
- [x] Task 109: 导出 Epic/Story 数据（GET /api/stories/{sid}/export 返回 Story 及所有子任务）

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-13 | Epic 20 | 批量任务操作（bulk-update/bulk-delete）+ 增强搜索排序（sort_by/sort_order）+ 多条件过滤（status[]/priority[]）+ 数据导出（project/story export）+ 36 项测试全绿 |
| 2026-07-14 | Epic 21 Story 21.1 | 健康检查 60s 轮询（可开关）+ 通知未读数 60s 轮询 + 离线提示条（online/offline 事件）；36 测试全绿 |
| 2026-07-14 | Epic 21 Stories 21.2-21.4 | API 缓存强化（stats 端点缓存 + 配置化 TTL）+ 批量操作 UX（Shift+点击多选 + 进度指示 + 失败反馈）+ 前端错误边界（全局错误处理 + 离线队列计数）；API 测试全绿 |

---

## Epic 21：平台稳定性与用户体验优化（v0.5）
> 目标：健康检查自动轮询、通知轮询、API 缓存强化、批量操作 UX 增强、前端错误处理优化。

### Story 21.1 健康检查与通知自动轮询
- [x] Task 400: 健康检查定时轮询（60s）+ 可开关存储在 localStorage
- [x] Task 401: 通知未读数自动轮询（60s）+ 面板打开时立即刷新
- [x] Task 402: API 离线检测：网络断开时显示离线提示条

### Story 21.2 API 缓存强化与性能优化
- [x] 扩展缓存到 stats 端点 + 配置化 TTL + 优化缓存失效逻辑

### Story 21.3 批量操作 UX 增强
- [x] 批量操作进度指示 + 失败反馈优化 + 批量选择快捷键

### Story 21.4 前端错误处理与离线支持
- [x] API 重试机制 + 离线状态提示 + 错误边界

---

## Epic 22：审计日志、任务依赖、Webhook 与数据导入（v0.5）
> 目标：API 审计日志、任务依赖关系管理、Webhook 配置、数据导入导出增强。

### Story 22.1 审计日志
- [x] Task：审计日志模型（audit_logs 表：user_id/action/entity_type/entity_id/method/path/ip/user_agent/response_status/duration_ms）
- [x] Task：API 中间件自动记录所有非 health/meta/auth 的 API 请求
- [x] Task：审计日志查询 API（GET /api/audit-logs，支持 entity_type/entity_id/user_id/action 过滤）

### Story 22.2 任务依赖关系
- [x] Task：任务依赖模型（task_dependencies 表：task_id/depends_on_id/dependency_type）
- [x] Task：依赖 CRUD API（POST /api/tasks/{tid}/dependencies、DELETE /api/dependencies/{did}）
- [x] Task：前端依赖管理面板（任务详情抽屉内：blockers/blocked_by 显示 + 添加/删除）

### Story 22.3 数据导入
- [x] Task：JSON 数据批量导入 API（POST /api/projects/{pid}/import）

### Story 22.4 Webhook 配置
- [x] Task：Webhook 配置模型（webhook_configs 表：project_id/name/url/secret/events/enabled）
- [x] Task：Webhook CRUD API（POST/GET/DELETE/PATCH /api/webhooks）
- [x] Task：前端 Webhooks Tab（项目详情页：列表/创建/启用停用/删除）

### Story 22.5 MCP 工具扩展
- [x] Task：MCP 审计日志工具（list_audit_logs）
- [x] Task：MCP 依赖管理工具（get_task_dependencies、remove_task_dependency）

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-14 | Epic 22 | 审计日志中间件（记录所有 API 请求）+ 任务依赖关系（blocks/blocked_by/relates_to）+ Webhook 配置（CRUD）+ 数据导入；修复 audit_log_middleware 使用 service.SessionLocal() bug；迁移 9f8c2e7d1a4c；前端依赖面板 + Webhooks Tab + Audit Logs 面板；MCP 工具扩展；commit f7ec4ea |

---

## Epic 27（Epic 15 in DB）：前端体验升级 v0.6（2026-07-14 创建）
> 目标：看板交互增强、搜索优化、详情页导航、无障碍访问。

### Story 27.1 看板卡片动画优化（Stories 40-44）
- [x] Task 700: 看板卡片 hover 动画增强（cubic-bezier spring + shimmer + `::before` 渐变）
- [x] Task 701: 看板列拖拽占位符动画（`::after` + `@keyframes` 指示器）

### Story 27.2 搜索体验优化
- [x] Task 702: 搜索框历史记录下拉（localStorage，最多 10 条）
- [x] Task 703: 搜索结果高亮关键词（`<mark>` 高亮 + 深色适配）

### Story 27.3 任务详情页增强
- [x] Task 704: 任务详情页上一条/下一条导航

### Story 27.4 无障碍访问优化
- [x] Task 706: 关键元素 ARIA 属性添加（`.skip-link`/`.sr-only`/`.live-region`）

### Story 27.5 API 性能优化
- [x] Task 705: API 响应缓存与防抖（300ms debounce on search）

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-14 | Epic 26 | Task 700/702/703/704/706 → in_review |
| 2026-07-14 | Epic 26 | Task 701/705 → in_review |

---

## Epic 29（Epic 17 in DB）：性能监控与键盘增强（2026-07-15 创建）
> 目标：性能指标显示、快捷键面板增强、批量操作键盘支持。

### Story 29.1 性能监控与优化提示（Story 45）
- [x] Task 708: 性能指标显示（加载时间/API响应时间/成功率）
- [x] Task 709: 慢操作提示（骨架屏/进度指示）

### Story 29.2 键盘导航增强（Story 46）
- [x] Task 710: 快捷键提示面板增强（批量操作快捷键说明）
- [x] Task 711: 任务列表批量选择键盘支持（Ctrl+A/Del）

### Story 29.3 动画与过渡优化（Story 47）
- [x] Task 712: 页面切换过渡动画

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-15 | Epic 29 | Task 708/710/711/712 → in_review（性能监控 + 快捷键增强） |

---

## Epic 28（Epic 16 in DB）：前端体验升级 v0.7（2026-07-15 完成）
> 目标：看板交互增强、虚拟滚动、全局快捷键。

### Story 28.1 看板卡片动画优化（Stories 49）
- [x] Task 712: 看板卡片 hover 效果增强（`will-change` + cubic-bezier(0.34,1.56,0.64,1) spring + shimmer `::before`）
- [x] Task 713: 卡片拖拽占位符动画（`.dragging`/`.drag-over`/`.drag-over::after` + `@keyframes drag-drop-indicator`）

### Story 28.2 列表渲染性能优化（Stories 50）
- [x] Task 714: 虚拟滚动优化大型列表（`taskPageSize`/`taskPageCount` signals + 加载更多按钮）
- [x] Task 715: 增量渲染优化（`tasksForStatus` memoize + Angular signals computed cache）

### Story 28.3 快捷键增强（Stories 51）
- [x] Task 716: 全局快捷键面板（`showShortcuts` signal + `shortcuts` data + `?` 键 + 顶部 `?` 按钮）

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-15 | Epic 28 | Tasks 712-716 → in_review；cubic-bezier spring hover + drag placeholder + virtual scroll + shortcuts panel |

---

## Epic 30：前端体验升级 v0.8（2026-07-15 创建）
> 目标：API 缓存强化、批量操作 UX 增强、前端错误处理。

### Story 30.1 API 缓存强化（Story 37）
- [ ] Task 801: 扩展 API 缓存 TTL 配置
- [ ] Task 802: 添加缓存命中率统计

### Story 30.2 批量操作 UX 增强（Story 38）
- [x] Task 803: 批量选择全选/取消功能（Ctrl+A 快捷键）
- [x] Task 804: 批量操作进度条显示（百分比+进度条）
- [x] Task 805: 批量操作结果反馈

### Story 30.3 前端错误处理与离线支持（Story 39）
- [x] Task 806: 全局错误边界组件
- [x] Task 807: 错误重试机制
- [x] Task 808: 离线操作队列可视化

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-15 | Epic 29 | Task 708/709 → in_review（性能指标 + 骨架屏） |
| 2026-07-15 | Epic 30 | Task 803/804/805/806/807/808 → in_review（批量操作 + 错误处理） |

---

## Epic 31（Epic 18 in DB）：前端体验升级 v0.9（2026-07-15 创建）
> 目标：通知搜索过滤、看板视觉增强、任务列表排序、项目卡片 3D 效果。

### Story 31.1 通知与看板增强
- [x] Task 727: 通知面板搜索过滤（`notifSearchQuery` signal + `filteredGroupedNotifications` computed）
- [x] Task 728: 看板列交替背景色（`nth-child(odd/even)` + color-mix 微妙区分）
- [x] Task 729: 看板卡片显示 Epic 名称徽章（`taskEpicName()` helper + CSS）
- [x] Task 731: 项目卡片 3D 悬浮效果 + 项目计数徽章

### Story 31.2 任务列表排序
- [x] Task 730: 任务列表排序下拉（`taskSortKey`/`taskSortOrder` signals + 按创建/更新/优先级/标题排序）

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-15 | Epic 31 | Tasks 727-731 → in_review（通知搜索 + 看板交替色 + Epic徽章 + 排序下拉 + 3D卡片） |
| 2026-07-17 | Epic 32 Story 32.1 Task 900 | B-04 看板拖拽排序完成：Story 看板 HTML5 drag-and-drop（`draggable` + `dragstart/dragover/drop`），调用现有 `PUT /api/tasks/{id}/status`，零后端契约变更；顺带修复 rate limiter 阻断 CORS preflight（OPTIONS 跳过）；Playwright E2E 通过：1/1 卡片 draggable、拖拽 待规划→待办 成功、零错误。Epic 103 / Story 163 / Task 862 全部 done |
| 2026-07-18 | Epic 34 Story 34.1 Task 903 | 任务列表汇总栏完成：Story 详情任务列表工具条下方加 `.task-list-summary`（状态分布堆叠条 + "共 N 项 · 已完成 X · 进行中 Y · 完成率 Z%"）；`taskListSummary()` computed 基于 `tasks()` 聚合（复用 `STATUS_COLOR` 段色）；仅列表模式显示、看板模式隐藏；仅 `app.ts`(+12)/`app.html`(+12)/`app.css`(+33)（净增 57 行，符合 R2 <80）；不改 `models.py`/`api.py` 契约；Playwright E2E（`tests/test_epic34_summary_e2e.py`）：`.task-list-summary` 渲染、3 段堆叠条、文案含 共/完成率、summary total(153) == task list rows(153)、切换看板→列表 摘要消失再重现、零 page/console/.js+.css 错误；回归 Epic 33 E2E 仍 PASS。Epic 24 / Story 60 / Task 831 全部 done |

## Epic 34（Epic 24 in DB）：前端体验升级 v1.4（2026-07-18 创建）
> 目标：任务列表信息密度向 Jira/Linear 靠拢；纯前端，不改后端契约。

### Story 34.1 任务列表汇总栏
- [x] Task 903: 任务列表汇总栏 - computed + 模板 + CSS

### 完成记录
| 日期 | 项 | 简述 |
|------|----|------|
| 2026-07-18 | Epic 34 | Task 903 → done（任务列表汇总栏：堆叠条 + 总数/完成率文案） |

