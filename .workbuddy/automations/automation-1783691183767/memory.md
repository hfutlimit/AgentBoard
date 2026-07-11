# AgentBoard 自动开发 — 执行记录

## 2026-07-12（周期执行 · Epic 7 前端登录/注册 UI）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=181fa99，B-06）；工作树 clean。
- **需求/任务分析**：Epic 11 纯前端 backlog 全完成；下一个高价值 pending = Epic 7 前端登录/注册（后端鉴权已就绪）。实测线上 API 当前**开放**（/api/projects=200），故采用"动态守卫"而非硬 gate，避免破坏开放部署。
- **开发任务**：`app.js`(+123/−3) 新增 token 生命周期（getToken/setToken/clearToken）+ CURRENT_USER 态；`api()` 注入 Authorization 并在 401 跳登录（auth 端点自身不触发防递归）；`showAuthScreen()/authScreenHTML()/bindAuthScreen()` 登录注册卡片（tab 切换、调 /api/auth/register|login、成功存 token、失败 toast）；启动用 /api/auth/me 校验；`updateUserInfo()` 顶栏用户名+登出、`logout()`、`startApp()`；`render()` 加 _AUTH_VISIBLE 守卫。`style.css`(+20) 补 .auth-* / .user-chip。`test_web_flow.py`(+12/−1) 增 Epic 7 静态断言并前移 css 获取（修 UnboundLocalError）。`docs/tasks.md` 勾选 Epic 7 三项 Story + 完成记录。未改 models.py/api.py 契约。
- **部署 Docker**：Docker Hub 不可达 → 退化 `docker cp` 注入新 app.js/style.css 到运行中的 agentboard-web-1（/app/agentboard/web/static/）。HTTP 校验 served app.js 含 `function showAuthScreen`、style.css 含 `.auth-card`；线上 API 实测 register→201+token、me(token)→200、me(无)→401、错误密码→401。
- **执行测试**：托管 venv 跑 tests/test_web_flow.py + test_backend_flow.py → **6 passed**（新增 Epic 7 断言），无回归。
- **推送**：commit `df20152`，`git push origin main` 成功（`181fa99..df20152`）。
- **下一个 pending 项**：Epic 8 MariaDB 独立 .sql 脚本 + 真实集成验证 / Epic 9 Playwright E2E / Epic 12.2~12.4。

## 2026-07-12（周期执行 · B-06 列表分组收尾交付）
- **拉取最新代码**：`git fetch origin` 显示远端无新提交（HEAD=df19997，0/0），已是最新；工作树含 B-06 列表分组未提交改动（app.js+75/−19、style.css+7、test_web_flow.py+3、docs/tasks.md 完成记录）。
- **需求/任务分析**：Epic 11 Backlog A（A-01~A-20）与 Backlog C（P-01~P-15）均已完成；Backlog B 中仅 B-06 的纯前端「按状态/按类型」分组部分已在树中写完但未提交。本周期收尾交付 B-06（不新开其他项，保持每周期一项）。
- **开发任务（已在工作树）**：Story 任务列表新增「不分组/按状态/按类型」`<select id="s-group-by">`，复用后端已返回的 status/type 字段、无需新 API；新增 `storyTaskItemHTML()/storyTaskListHTML()`；分组偏好存 `localStorage`（键 `agentboard_story_group`）；全局搜索过滤后自动隐藏空分组标题。净增 ~53 行（app.js+46/−18、style.css+7、test+2），符合 R2，未改 `models.py`/`api.py` 契约。
- **部署 Docker**：`docker compose build web` 因沙箱 Docker Hub 不可达（`python:3.13-slim` 元数据拉取超时）失败；退化为 `docker cp` 将新 `app.js`/`style.css` 注入运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。⚠️ 副作用修复：`up -d web` 传入的占位 `AGENTBOARD_SECRET` 与 api 原密钥不同，导致 `agentboard-api-1` 被重建为占位密钥（与未运行的 mcp 潜在冲突）；已写入 `.env`（`AGENTBOARD_SECRET`，已被 gitignore）固化稳定密钥并 `docker compose up -d api web` 重建 api 一致化，根治后续 mcp 启动冲突。HTTP 校验 page 200、served app.js 含 `storyTaskListHTML`/`id="s-group-by"`/`agentboard_story_group`、style.css 含 `.group-head`/`.select-sm`、`/api/meta`(8000) 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归（排除需 fastmcp 的 test_smoke）。
- **推送**：`git commit` 暂存 B-06 四文件 + 自动化/项目记忆 → `git push origin main`。
- **下一个 pending 项**：Backlog B 剩余 B-01 labels / B-02 assignee / B-03 due_date / B-04 看板拖拽 均需后端契约改动；可转 Epic 7 登录 UI / Epic 9 Playwright E2E / Epic 8 MariaDB 脚本等。

## 2026-07-11（周期执行 · A-20 前端偏好本地存储 + 收尾 Backlog A）
- **拉取最新代码**：`git pull origin main` 首次 SSH 中断，重试成功，已是最新（HEAD=a853813，即 A-19）。工作树含用户未提交无关改动（MCP 鉴权/MariaDB 等 ~448 行 + 未跟踪 examples/、openspec/changes/archive/、tests/test_mcp_smoke.py），未动。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-19 已完成，认领最后一个 pending 项 **A-20 前端偏好本地存储**（记住 Story 页列表/看板视图）。
- **开发任务**：Story 页任务区视图切换经 `localStorage`（键 `agentboard_story_view`）持久化——`storyViewMode` 启动读取、切换回写，下次进入 Story 页自动恢复。`app.js`+3/−1（新增 `VIEW_KEY` 常量与读取/回写两处）、`test_web_flow.py`+1（静态断言），净增 ~4 行，符合 R2，未改 `models.py`/`api.py` 契约。
- **部署 Docker**：`docker cp` 注入新 `app.js` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/，避免把用户无关改动烤进重建镜像，因运行镜像由旧提交构建）。HTTP 校验 page 200、served app.js 含 `const VIEW_KEY`/`localStorage.setItem(VIEW_KEY`、`/api/meta`(8000) 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git commit` **仅暂存 `app.js`+`test_web_flow.py`**（刻意不提交 `docs/tasks.md`——该文件同时含用户未提交无关改动，避免混入）；`git push origin main` 成功（`a853813..cf429b1`，commit `cf429b1`）。
- **⚠️ 未提交项**：`docs/tasks.md` 的 A-20 勾选/完成记录已写入工作树但未提交（与用户未提交改动纠缠），待用户提交其部分或下个周期一并带上。
- **下一个 pending 项**：Backlog A 已全完成（A-01~A-20）。Backlog B 均需后端契约改动，不可纯前端做。下一周期需重新评估轨道：可转入 Backlog B 中"部分纯前端实现"的子项、或 Epic 7 登录 UI / Epic 9 Playwright E2E 等。

## 2026-07-11（周期执行 · A-19 列表项 hover 操作 + 修复 story 行内编辑 404）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=0818e74）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-18 已完成，认领下一个 pending 项 **A-19 列表项 hover 操作**。
- **开发任务**：Epic/Story/Task 列表项右侧加 hover/focus-within 淡入的「✏ 编辑 / 🗑 删除」快捷图标（触摸设备常显）。新增 `entityActions()` 渲染 + `attachEntityActions()` 委托（`preventDefault+stopPropagation` 防导航/抽屉；编辑复用 `inlineEditEnter`，删除 confirm 后调既有 DELETE 端点 + `render()`）；新增 `API_PLURAL` 复数映射，**顺手修复 `inlineEditEnter` 对 story 生成 `/api/storys`(404) 的既有缺陷**。`app.js`+~35、style.css+8、test+2（净增 ~45 行，符合 R2），未改契约。
- **部署 Docker**：base image `python:3.13-slim` 未缓存、Docker Hub 不可达 → 退化 `docker cp` 注入 app.js/style.css 到 `agentboard-web-1`。HTTP 校验 page 200、served app.js 含 `entityActions`/`attachEntityActions`(4)/`API_PLURAL`、style.css 含 `.entity-item-actions`/`.ei-act`。
- **执行测试**：托管 venv 跑 test_web_flow + test_backend_flow → **6 passed**（新增 A-19 静态断言），无回归。
- **推送**：见下方 commit。
- **下一个 pending 项**：A-20 前端偏好本地存储（记住上次视图 列表/看板 等偏好）。

## 2026-07-11（周期执行 · 收尾创建弹窗 + A-18 面包屑高亮）
- **拉取最新代码**：`git pull origin main` 首次 SSH 中断，重试成功（已是最新，HEAD=e6e8a0b）；工作树有前次会话遗留的未提交「统一创建弹窗」重构（app.js/style.css/test_web_flow.py，约 +112/−107），非本次新增。
- **遗留改动收尾**：该重构为完整功能（统一 `showCreateModal(kind,parentId,context)` 替换内联新增表单，含遮罩/Esc/焦点归还/校验/Ctrl·⌘+Enter），`node --check` 通过、托管 venv 跑 `test_web_flow.py`+`test_backend_flow.py` → **6 passed**（测试已含 `showCreateModal`/`data-modal-close` 断言）。提交 `adeb637` 并推送成功（`e6e8a0b..adeb637`）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-17 已完成，认领下一个 pending 项 **A-18 面包屑高亮当前级**。
- **开发任务**：`.crumb-current` 由纯文字改为品牌浅底药丸（bold + `--brand-soft` 背景 + 1px 品牌环），链接面包屑加 hover 浅底 chip 与 `:focus-visible` 品牌光环，当前级加 `aria-current="page"`。`app.js`+1、style.css+16/−5、test_web_flow.py+2（净增 ~13 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 本地未缓存、Docker Hub 不可达 → `docker compose up -d --build` 会拉取失败；退化为 `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 page 200、served app.js 含 `aria-current="page"`/`crumb-current`、served style.css 含 `.crumb-current`/`var(--brand-soft)`。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`adeb637..0818e74`，commit `0818e74`）。`docs/tasks.md` 勾选 A-18 并补「创建弹窗重构」「A-18」完成记录。
- **下一个 pending 项**：A-19 列表项 hover 操作（hover 显示「编辑/删除」快捷图标）或 A-20 前端偏好本地存储。

## 2026-07-11（周期执行 · P-15 Agent 活动面板）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=8f604b1）。
- **需求/任务分析**：Epic 11 Backlog C 顺序推进；P-01~P-14 已完成，认领下一个 pending 项 **P-15 Agent 活动面板（可选）**（依赖 P-10，复用 `avatar()`）。
- **开发任务**：纯前端实现，未改 `models.py`/`api.py` 契约（R2）。`viewHome` 在统计循环内收集全部 task，经现有 `/api/tasks/{id}/comments` 用 `Promise.all` 并行拉取评论，按 `created_at` 取近 12 条聚合为「近期动态 / Agent 活动」时间线；`.dashboard` 转 `1fr 330px` 双栏，面板 `grid-row:1/-1` sticky 跨列、`≤1000px` 单列堆叠；新增 `timeAgo()`/`activityPanel()` 复用 `avatar()`。`app.js`+~52/`style.css`+~23（净增 ~75 行，符合 R2），`test_web_flow.py` 增 `activity-panel`/`timeAgo` 静态断言。
- **部署 Docker**：基础镜像本地已缓存 → `docker compose build web` + `up -d web` 规范重建成功（容器 recreated 且 healthy），无需退化 `docker cp`。HTTP 校验 page 200、served app.js 含 `activityPanel`/`timeAgo`/`activity-avatar`、served style.css 含 `.activity-panel`/`minmax(0,1fr) 330px`、`/api/meta`(8000) 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 首次因沙箱 SSH 不可达失败（`Connection closed by 198.18.0.18 port 22`），重试成功（`8f604b1..72a456f`，commit `72a456f`）。
- **下一个 pending 项**：Backlog A 中 **A-18 面包屑高亮当前级**（纯前端，补样式；或 A-19 列表项 hover 操作 / A-20 前端偏好本地存储）。

## 2026-07-11（手动收尾 · 品牌组件系统 P-04~P-14 批量完成）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=fab47c8）。
- **未完成任务盘点**：工作树有一批未提交、未部署的大块 UI 重设计改动（app.js+126/−51、style.css+120、index.html+4/−4、tests/test_web_flow.py+6），对应 Backlog C 的 P-04~P-14 多项，疑似前次会话中断遗留；非本次新增开发。
- **校验**：`node --check app.js` 语法 OK；托管 venv 跑 `tests/test_web_flow.py`+`test_backend_flow.py` → **6 passed**（含新增 `hero`/`badge-dot`/`empty-art`/`--grad`/`dark` 断言）。
- **部署 Docker**：基础镜像 `python:3.13-slim` 本地未缓存、Docker Hub 不可达 → `docker compose up -d --build web` 拉取失败；退化 `docker cp` 注入 `app.js`/`index.html`/`style.css` 到运行中的 `agentboard-web-1`。HTTP 校验 page 200、served app.js 含 `function avatar`/`class="hero"`/`badge-dot`/`empty-art`、served style.css 含 `backdrop-filter`(3)/`stat-rate`(6)/`project-progress`(4)/`--grad:`(2)。
- **任务勾选**：`docs/tasks.md` 勾选 P-04~P-14（顶栏磨砂/统计卡/项目卡进度/徽章点/优先级SVG/空状态插画/头像/按钮聚焦/表面分级/暗色同步/Hero）。**P-15 Agent 活动面板未实现，保持 pending。**
- **推送**：`git push origin main`（待执行，commit 见下）。下一个 pending 项：**P-15 Agent 活动面板**（依赖 P-10，复用 `avatar()`）。

## 2026-07-11（周期执行 · P-03 Logo Mark 与品牌字）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=132641a）。
- **需求/任务分析**：Epic 11 Backlog C 顺序推进；P-01/P-02 已完成，认领下一个 pending 项 **P-03 Logo Mark 与品牌字**（依赖 P-01，已满足）。
- **开发任务**：`index.html` 加 data URI SVG favicon（渐变看板图标）+ 将纯文字 `<h1 class="logo">AgentBoard</h1>` 替换为内联渐变 SVG 看板 mark（`<span class="logo-mark">`）+ 渐变描边文字 `<span class="logo-text">Agent<b>Board</b></span>`（复用 P-01 `--grad`/`--brand-ring`）；`style.css` 重写 `.logo` 为 inline-flex，新增 `.logo-mark`（drop-shadow 品牌光晕）/`.logo-text`（background-clip:text 渐变字 + `<b>` 加粗）。`index.html`+3、`style.css`+10（净增 ~13 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 本地未缓存、Docker Hub 不可达 → `docker compose up -d --build web` 拉取失败；退化 `docker cp` 注入新 `index.html`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 page 200、served index.html 含 `logo-mark`/`logo-text`/`rel="icon"`、served style.css 含 `logo-mark`(1)/`logo-text`(2)/`background-clip`(2)。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`132641a..fab47c8`，commit `fab47c8`）。
- **下一个 pending 项**：P-04 顶栏磨砂与导航胶囊（`.topbar` backdrop-filter 磨砂 + 导航 active 胶囊 + 搜索框聚焦品牌光环，依赖 P-01）。

## 2026-07-11（周期执行 · P-02 字体与排版升级）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=1146d1a）。
- **需求/任务分析**：Epic 11 Backlog C 顺序推进；P-01 已完成，认领下一个 pending 项 **P-02 字体与排版升级**。
- **开发任务**：`index.html` 加 Inter + JetBrains Mono Google Fonts `<link>`（系统栈兜底、离线降级）；`style.css` `:root` 新增 `--font-sans`/`--font-mono`，`body` 用 `var(--font-sans)`，标题 `h2/h3/h4` `letter-spacing:-.02em`，`.stat-number`/`.sidebar-key`/`.progress-pct`/`.kanban-count` 加 `tabular-nums`，`textarea`/`.md pre`/`.md code` 用 `var(--font-mono)`。`index.html`+3/`style.css`+3（净增 ~6 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 仍不在本地缓存、Docker Hub 不可达 → `docker compose up -d --build web` 会失败；退化 `docker cp` 注入新 `index.html`/`style.css` 到 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 page 200、style.css 含 `--font-sans`(2)/`tabular-nums`(4)/`letter-spacing: -.02em`(3)/`var(--font-mono)`(3)、index.html 含 `fonts.googleapis.com`(2)/`JetBrains+Mono`(1)。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`1146d1a..8e07f64`，commit `8e07f64`）。
- **下一个 pending 项**：P-03 Logo Mark 与品牌字（内联 SVG 看板图标 + 渐变描边文字，加 favicon；依赖 P-01）。

## 2026-07-11（周期执行 · P-01 设计 Token 体系）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=12be63e；Backlog C 已接入，候选首项 P-01）。
- **需求/任务分析**：Epic 11 Backlog C 为本轮优先；A-01~A-17 已完成，P-01 为 Backlog C 首个 pending 项，认领 **P-01 设计 Token 体系**。
- **开发任务**：`style.css` `:root`+`[data-theme="dark"]` 落地 `--brand-500/600/700`/`--brand-soft/ring`/`--grad`/`--success/--warning/--danger/--info/--violet`/`--text-2/3`/`--border-2`/`--surface-2/3`/`--sh-sm/md/lg/brand`/`--r-sm/md/lg`；`--primary` 保留为 `--brand-500` 别名、`--text-secondary`/`--card-bg` 续用旧名以兼容旧类。`style.css` +74/−10（净增 ~64 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 本地未缓存、Docker Hub 不可达 → `docker compose up -d --build web` 会触发拉取失败；退化 `docker cp` 注入新 `style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 served style.css 含 `--brand-500`(6)/`--surface-2`(2)/`--sh-brand`(2)/`data-theme="dark"`(24)、page 200、`/api/meta` 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`12be63e..1146d1a`，commit `1146d1a`）。
- **下一个 pending 项**：P-02 字体与排版升级（依赖 P-01）。

## 2026-07-11（周期执行 · A-17 路由过渡动画）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=316378c）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-16 已完成，认领下一个 pending 项 **A-17 路由过渡动画**。
- **开发任务**：复用既有 `fadeIn` keyframe；`render()` 末尾对 `#app` 先 `classList.remove("route-in")` + 强制回流(`void app.offsetWidth`) + `classList.add("route-in")`，使每次视图切换后主内容区淡入+轻微上滑（.22s）；`style.css` 加 `.route-in { animation: fadeIn .22s ease-out; }` 与 `prefers-reduced-motion` 降级。`app.js`+4、`style.css`+3（净增 7 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 本地未缓存、Docker Hub 不可达 → `docker compose up -d --build web` 元数据解析失败；退化为 `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 page 200、`/api/meta` 200、`app.js` 含 `route-in`(2)、`style.css` 含 `route-in`(2) 与 `fadeIn`(5)。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main`（待执行，commit 见下）。
- **下一个 pending 项**：A-18 面包屑高亮当前级（确保各级面包屑链接正确且高亮当前级，补样式）。

## 2026-07-11（周期执行 · A-16 复制链接）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=f12958c）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-15 已完成，认领下一个 pending 项 **A-16 复制链接**。
- **开发任务**：Task 详情页与 Story 详情页 `page-actions` 各加「🔗 复制链接」按钮；新增 `copyLink(href)`（组装 `origin+pathname+#/xxx` 深链，优先 `navigator.clipboard.writeText`，回退 `execCommand` 临时 textarea）+ `fallbackCopy()`，复制成功 `toast("已复制链接")`。`app.js`+24/−0（净增 ~24 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 仍不在本地缓存、Docker Hub 不可达 → `docker compose up -d --build web` 元数据解析失败；退化 `docker cp` 注入新 `app.js` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 page 200、served app.js 含 `function copyLink`(1)/`fallbackCopy`(3)/`copy-task-link`+`copy-story-link`(4)。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`f12958c..316378c`，commit `316378c`）。
- **下一个 pending 项**：A-17 路由过渡动画（视图切换加淡入/滑入过渡）。
- **环境笔记**：① `/api/*` 经 Web 容器(8080)不代理，SPA 直连 API(8000)，故 8080 上 `/api/meta` 404 为预期、非回归；② 工作树 `.workbuddy/memory/2026-07-11.md` 曾被无关「UI 重设计提案」内容覆盖，已从 HEAD 还原后再追加 A-16 记录，避免丢失自动开发历史。

## 2026-07-11（周期执行 · A-15 键盘快捷键）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=d3a2bdd）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-14 已完成，认领下一个 pending 项 **A-15 键盘快捷键**。
- **开发任务**：新增全局 `keydown` 监听（`j`/`k` 上下移动选中项、`e` 编辑选中项、`Esc` 关闭弹层由既有监听处理），复用 `inlineEditEnter()`/`route()`；`kbdItems()/kbdSet()/kbdEdit()` 在 `.entity-item/.project-card/.kanban-card` 管理选中态（`.kbd-selected` 高亮 + scrollIntoView），输入框聚焦或带修饰键时跳过，`render()` 重置选中态；顶栏加 `⌨ j/k · e · Esc` 提示。`app.js`+41、style.css+8、index.html+1（净增 50 行，符合 R2），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 仍不在本地缓存、`docker compose build` 拉取超时 → 退化 `docker cp` 注入新 `app.js`/`style.css`/`index.html` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 app.js 含 `kbdItems`/`kbdSet`/`kbdEdit`(3)、style.css 含 `kbd-selected`/`kbd-hint`(6)、index.html 含 `kbd-hint`(1)、`/api/meta` 200、页面 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main`（待执行，commit 见下）。
- **下一个 pending 项**：A-16 复制链接（任务/Story 提供「复制深链」按钮）。

## 2026-07-11（周期执行 · A-14 Markdown 编辑工具栏）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=faef13a）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-13 已完成，认领下一个 pending 项 **A-14 Markdown 编辑工具栏**。
- **开发任务**：新增 `mdToolbar(taName)`/`insertMd(ta,kind)`/`bindMdToolbar(scope)` 三个辅助；Task 详情编辑表单的 description/spec 文本框上方加「加粗/标题/列表/行内代码」按钮（行内类包裹选区、块级类行首插入，含占位文本与自动选中），点击即插入 markdown 语法。`app.js`+43/−2、`style.css`+8（净增 ~49 行，符合 R2），未改 `models.py`/`api.py` 契约；工具栏样式随 CSS 变量适配深色模式。
- **部署 Docker**：基础镜像 `python:3.13-slim` 本地未缓存、Docker Hub 不可达 → `docker compose up -d --build web` 元数据解析失败；退化为 `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。HTTP 校验 app.js 含 `function mdToolbar`(1)、style.css 含 `md-toolbar`(1)、页面 200、`/api/meta` 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`faef13a..d3a2bdd`，commit `d3a2bdd`）。
- **下一个 pending 项**：A-15 键盘快捷键（j/k 上下移动选中项、e 编辑、Esc 关闭弹层）。

## 2026-07-11（周期执行 · A-13 任务详情抽屉）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=d21e14b）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-12 已完成，认领下一个 pending 项 **A-13 任务详情抽屉**。
- **开发任务**：Story 页任务列表/看板项由 `<a href="#/task/{id}">` 改为 `<a data-task-id>`（去 href，不跳路由）；新增 `openTaskDrawer`/`closeTaskDrawer`/`attachTaskDrawer` 三个辅助——单击从右侧滑出抽屉（含 description/spec + 状态流转按钮，复用 `md()/statusFlow()/statusBadge()` 等），遮罩点击/Esc 关闭并 `render()` 刷新列表；列表项保留 A-04 双击编辑标题（200ms 计时区分单击/双击）。index.html 加 `#drawer-overlay`/`#task-drawer` 容器，style.css 加 `.drawer*` 样式（含暗色兜底）。`app.js`+~80、`style.css`+32、index.html+2（净增 ~114 行，含 CSS/HTML，前端逻辑 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像本地缓存命中 → `docker compose up -d --build web` 规范重建成功（api/web 镜像重建、容器 recreated 且 healthy），无需退化 `docker cp`。HTTP 校验 page 200、`/api/meta` 200、`app.js` 含 `openTaskDrawer`(3)、`style.css` 含 `.drawer`(15)、index 含 `task-drawer`(1)。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`d21e14b..d7d3a1c`，commit `d7d3a1c`）。
- **下一个 pending 项**：A-14 Markdown 编辑工具栏。

## 2026-07-11（周期执行 · A-12 Toast 堆叠与动画）
- **拉取最新代码**：`git pull origin main` 因沙箱 SSH 不可达失败（`Connection closed by 198.18.0.18 port 22`）；先把上一轮残留的自动化/项目记忆文件提交（`7500bac`），本地领先 origin。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-11 已完成，认领下一个 pending 项 **A-12 Toast 堆叠与动画**。
- **开发任务**：`#toast` 由单一覆盖元素改为多子项容器，每条提示独立 `.toast-item`（滑入淡入进场、2.5s 后淡出移除，多条约提示互不覆盖），支持可选 `type=error|success` 左侧色条。`app.js`+10、`style.css`+13（净增 ~23 行，符合 <~80 行），未改 `models.py`/`api.py` 契约，调用方向后兼容。
- **部署 Docker**：**基础镜像本地缓存命中** → `docker compose up -d --build web` 规范重建成功（api/web 镜像重建、容器 recreated 且 healthy），无需退化 `docker cp`。HTTP 校验 `app.js` 含 `toast-item`(2)、`style.css` 含 `toast-item`(5)、`/api/meta` 返回 200、页面 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`ac4195d..37cacde`，含 `7500bac`/`2cc558f`/`37cacde` 三个提交）。
- **下一个 pending 项**：A-13 任务详情抽屉（点击列表项从右侧滑出详情抽屉，不跳路由）。

## 2026-07-11（周期执行 · A-11 响应式布局）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=dea5edaf）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-10 已完成，认领下一个 pending 项 **A-11 响应式布局**。
- **开发任务**：纯 `style.css` 响应式增强——`≤768px` 时 `.layout` 转纵向、侧栏（树列表）堆叠为内容上方带 `max-height:42vh` 的可滚动面板（保留 ☰ 折叠）；按钮统一 `min-height:36px` 触摸目标、`.page-actions` 换行防溢出；`≤480px` 看板转 2 列、搜索框收窄。`style.css` +25/−3（净增 ~22 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像仍不可达 → `docker cp` 注入新 `style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 HTTP `style.css` 含 `A-11 响应式`（1 处）、`min-height: 36px`（1 处）、`/api/meta` 返回 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`dea5edaf..84efd1d`，commit `84efd1d`）。

## 2026-07-11（周期执行 · A-09 进度条）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=2f1a4c0）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-08 已完成，认领下一个 pending 项 **A-09 进度条（Epic/Story）**。
- **开发任务**：新增 `progressBar(done,total)` 辅助（细条+百分比，颜色随完成度变化：100%绿/≥50%蓝/<50%灰，total=0 不显示）；`viewProject` 聚合每个 Epic 下所有 Story 的任务完成度（epics→stories→tasks）、`viewEpic` 计算每个 Story 的任务完成度（stories→tasks），卡片底部渲染进度条。`.entity-item` 加 `flex-wrap` 容纳换行进度条。`app.js` +30/−2、`style.css` +8（净增 ~38 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像仍不可达 → `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 HTTP `app.js` 含 `progressBar`（3 处）、`style.css` 含 `entity-progress`（1 处）、`/api/meta` 返回 200。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`2f1a4c0..4e6df58`，commit `4e6df58`）。

## 2026-07-11（周期执行 · A-10 深色模式开关）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=4e6df58）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-09 已完成，认领下一个 pending 项 **A-10 深色模式开关**。
- **开发任务**：基于 CSS 变量切换明/暗主题——`[data-theme="dark"]` 覆盖 `--text/--bg/--card-bg/--border/--primary` 等变量，并对硬编码浅色表面（输入框/看板卡/分段按钮/幽灵按钮/代码行内）、hover 态、骨架屏占位条做兜底；顶栏 🌙/☀ 按钮点击切换，偏好存 `localStorage`（键 `agentboard_theme`）启动即应用。`app.js`+20、`style.css`+33、index.html+1（净增 ~54 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像仍不可达 → `docker cp` 注入新 `app.js`/`style.css`/`index.html` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 HTTP `app.js` 含 `toggleTheme`/`applyTheme`/`THEME_KEY`（8 处）、`style.css` 含 `data-theme="dark"`（22 处）、`index.html` 含 `theme-toggle`（1 处）。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`4e6df58..4b48974`，commit `4b48974`）。

## 2026-07-11（周期执行 · A-08 空状态优化）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=5040b96）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-07 已完成，认领下一个 pending 项 **A-08 空状态优化**。
- **开发任务**：新增统一 `emptyState(icon,title,desc,cta)` 辅助，将 Epic/Story/Task 列表空态由灰色「暂无」升级为「图标 + 引导文案 + 新建按钮」；CTA 通过 `document.getElementById(id).click()` 触发同页已有「＋ 新建」按钮。新增 `empty-compact` 紧凑型 CSS。`app.js` +19/−3、`style.css` +4（净增 ~23 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 不在本地缓存、Docker Hub 不可达 → `docker compose up -d --build web` 元数据 TLS 超时失败；退化为 `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 HTTP `app.js` 含 `function emptyState`（1 处）、`style.css` 含 `empty-compact`（3 处）。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`5040b96..2b29ab7`，commit `2b29ab7`）。

## 2026-07-11（周期执行 · A-07 加载骨架屏）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=2519dee）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-06 已完成，认领下一个 pending 项 **A-07 加载骨架屏**。
- **开发任务**：新增 `skeleton()` 占位（标题条 + 6 卡片网格 shimmer + 侧栏占位），`render()` 与 `index.html` 初始态均替换原「加载中…」/3-dot spinner，避免内容载入时的布局跳动。`app.js` +12、`style.css` +16、`index.html` +9（净增 ~35 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像仍不可达 → `docker cp` 注入新 `app.js`/`style.css`/`index.html` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 HTTP `app.js` 含 `function skeleton`、`style.css` 含 `sk-line`、`index.html` 含 `sk-grid`。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`2519dee..5040b96`，commit `5040b96`）。

## 2026-07-11（周期执行 · A-06 状态流转按钮组）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=5359dfc）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-05 已完成，认领下一个 pending 项 **A-06 状态流转按钮组**。
- **开发任务**：任务详情的「状态下拉 + 更新状态按钮」替换为 Jira 式工作流按钮组——当前状态药丸（`sf-current`）+ 合法迁移按钮（`sf-btn`，`STATUS_TRANSITIONS` 镜像后端 `service.TRANSITIONS`），点击即 `PUT /api/tasks/{id}/status`；后端仍为权威校验（非法迁移 400）。新增 `STATUS_TRANSITIONS` 常量与 `statusFlow()` 辅助；`app.js` +22/−7、`style.css` +17（净增 ~32 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 仍不在本地缓存 → `docker compose up -d --build` 会拉取超时；继续 `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`。验证 `http://localhost:8080/static/app.js` 含 `statusFlow`（2 处）、`style.css` 含 `sf-btn`（10 处）。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`5359dfc..6133905`，commit `6133905`）。

## 2026-07-11（周期执行 · A-05 全局搜索框）
- **拉取最新代码**：`git pull origin main` 已是最新（HEAD=4e5658a）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01~A-04 已完成，认领下一个 pending 项 **A-05 全局搜索框**。
- **开发任务**：顶部栏 `.topbar-right` 加 `type=search` 输入框（`index.html` +1）；新增 `applySearch()` 按标题实时过滤当前页列表容器（`.project-grid`/`.entity-list`/`.table-wrap`/`#story-board-view`），空结果显示「未找到匹配」提示，查询词跨路由持久化（全局 `GLOBAL_SEARCH`，render() 末尾重应用），boot 绑定 input 事件。`app.js` +38、`style.css` +13（净增 ~52 行，符合 <~80 行），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：基础镜像 `python:3.13-slim` 不在本地缓存，`docker compose up -d --build web` 将触发 Docker Hub 拉取超时；退化为 `docker cp` 注入 `app.js`/`style.css`/`index.html` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 HTTP `http://localhost:8080/static/app.js` 含 `applySearch`（3 处）、`index.html` 含 `global-search`（1 处）。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`4e5658a..8e5c338`，commit `8e5c338`）。

## 2026-07-11（周期执行 · A-04 行内编辑）
- **拉取最新代码**：`git pull origin main` 已是最新（与 origin/main 同步，HEAD=306ea21）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；A-01/A-02/A-03 已完成，认领下一个 pending 项 **A-04 行内快速编辑标题**。
- **开发任务**：新增 `inlineEditEnter`/`makeInlineEditable`/`makeInlineEditableDetail`/`attachInlineEditList` 辅助。`attachInlineEditList` 按锚点 href 推断 type/id，为 Epic/Story/Task 列表项标题挂载双击编辑；列表项位于 `<a>` 内，用单击导航/双击编辑计时（200ms）区分，避免双击先触发跳转销毁元素。Task 详情 `h2#task-title` 双击编辑并同步面包屑。回车/失焦 PATCH 保存、Esc 取消。改动 `app.js` +69/−1、`style.css` +10（净增 ~79 行，符合 <~80 行红线），未改 `models.py`/`api.py` 契约。
- **部署 Docker**：`docker compose up -d --build web` 仍因沙箱无法连通 Docker Hub（拉取 `python:3.13-slim` 元数据 TLS 超时）失败；退化为 `docker cp` 注入新 `app.js`/`style.css` 到运行中的 `agentboard-web-1`（/app/agentboard/web/static/）。验证 `http://localhost:8080/static/app.js` 含 A-04 标记（11 处）、`style.css` 含 `inline-edit-input`（4 处）。
- **执行测试**：托管 venv 跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`306ea21..4e5658a`，commit `4e5658a`）。
- **环境笔记（续）**：① Docker Hub / GitHub 在沙箱不可达 → 继续用 docker cp 注入 + 阿里云镜像；② 列表项双击编辑与单击导航的计时冲突已用 200ms 计时器解决；③ 净增行数恰在 ~80 边界，后续若超需拆子项（R4）。

## 2026-07-11（周期执行）
- **拉取最新代码**：本地 `git pull origin main` 已是最新；但工作树残留上一轮未提交的 A-02（状态色徽章）改动（app.js/style.css/docs/tasks.md + 记忆文件）。
- **需求/任务分析**：Epic 11 Backlog A 顺序推进；本轮先补齐遗留的 **A-02**（提交），再认领下一个 pending 项 **A-03 任务类型图标**。
- **开发任务**：新增 `typeIcon()` 内联 SVG 辅助（task=勾选圆环 / bug=瓢虫），替换看板/列表/任务详情/类型徽章中的 emoji；`app.js` +24/−5、`style.css` +5/−1（共 23 行新增，符合 <80 行）。未改动 `models.py`/`api.py` 契约。
- **提交**：A-02 单独提交 `94f66ea`；A-03 提交 `fd71dfd`。tasks.md 勾选 A-02/A-03 并追加完成记录。
- **部署 Docker**：Docker Hub 拉取 `python:3.13-slim` 基础镜像超时（沙箱网络），`docker compose up -d --build web` 失败；退化为 `docker cp` 将新 `app.js`/`style.css` 注入运行中的 `agentboard-web-1` 容器（/app/agentboard/web/static/）。验证 `http://localhost:8080/static/app.js` 含 `typeIcon`（5 处）。
- **执行测试**：用托管 venv（`/c/Users/.../envs/default/Scripts/python.exe`，阿里云镜像装的 fastapi/uvicorn/httpx/sqlalchemy/pytest 等，排除 fastmcp）跑 `tests/test_web_flow.py` + `tests/test_backend_flow.py` → **6 passed**，无回归。
- **推送**：`git push origin main` 成功（`275cc9d..fd71dfd`）。
- **环境笔记（续）**：① Windows 下 venv 路径是 `Scripts\` 不是 `bin\`；② 路径在 Git Bash 用 `/c/Users/...` POSIX 写法，不能用 `C:/...`；③ Docker Hub / 默认 PyPI 仍不可达，继续用 docker cp 注入 + 阿里云镜像。

## 2026-07-10（周期执行）
- **拉取最新代码**：仓库已是最新（clean，与 origin/main 同步）。
- **需求/任务分析**：`docs/tasks.md` 中 Epic 11 为前端持续优化轨道（Backlog A 纯前端小优化）；选取首个 pending 项 **A-01 看板视图** 作为本周期交付。
- **开发任务**：在 Story 详情页实现「看板/列表」视图切换——按 `META.statuses` 分列展示 task 卡片（只读，无拖拽）。仅改动 `app.js`(+70/−19) 与 `style.css`(+17)，复用既有 `/api/stories/{id}/tasks` 数据与 `statusBadge()`/`type` 渲染辅助，未改动 `models.py`/`api.py` 契约，符合迭代纪律（单文件为主、<~80 行、无新依赖）。
- **部署 Docker**：沙箱**无法连通 Docker Hub**（拉取基础镜像 TLS 握手超时），`docker compose up -d --build` 失败。改用 `docker cp` 将新 SPA 注入已运行的 `agentboard-web-1` 容器（路径 `/app/agentboard/web/static/`），已验证 `http://localhost:8080/static/app.js` 含新 `renderKanban` 代码。
- **执行测试**：默认 PyPI 源在沙箱极慢/挂起（fastmcp 在 Python 3.13 下构建挂起）；改用阿里云镜像 `mirrors.aliyun.com/pypi/simple` 后正常安装测试依赖。结果：Web 流程测试 **3 passed**；完整套件 **9 passed / 3 failed**。3 个失败均为 `tests/test_smoke.py` 的 MCP 测试（`ModuleNotFoundError: fastmcp`），属可选依赖、与本次前端改动无关，非回归。
- **提交与推送**：本地 `git commit` 成功（`275cc9d`，4 文件 +74/−19）。首次 `git push origin main` 因沙箱网络不可达失败；**用户授予 GitHub 访问权限后重试成功**（`27b30c8..275cc9d main->main`）。
- **环境笔记**：① PyPI 默认源慢 → 用阿里云镜像；② Docker Hub / GitHub 在沙箱不可达；③ fastmcp 在 3.13 下安装挂起，测试环境已排除该依赖。
