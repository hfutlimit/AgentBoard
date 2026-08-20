# Epic 150 / Story 322 (X1) — Prototype 还原踩坑（2026-08-20）

## 背景

X1 Home Shell Master-Detail 重构分 3 个子 PR：
- PR 1（7365cab）：新建 `<app-home-shell>` 组件 + 注册到 home case
- PR 2（80a17d9）：删除 home view 旧 dashboard + class 重命名（避免与 marketing 冲突）
- PR 3（773259a）：接 `[visible]="view() === 'home'"` + 根 `*ngIf="visible"`，4 个 view 切换验证
- PR 3 follow-up（0b329b6）：**隐藏 home view 的外层 sidebar / topbar**

## 关键坑

### 1. 只盯 home-shell 内部，漏掉外层 layout chrome

PR 1-3 一直关注的是 home-shell 内部 vs 删除 dashboard，**完全漏掉了**：
- app.html:30 的外层 `<header class="topbar">`（含汉堡 + 通知/头像）
- app.html:196 的外层 `<aside id="sidebar">`（项目协作中心 / 仪表盘 / Agents / 项目 / 项目列表树）

原型 `01-home-projects-1440.png` 里 home view **完全没有这两个外层组件**——home-shell 提供自己的 topbar。

**规则 → 任何 view 重构都要先看 prototype 截图，不光看 prototype html。**
**证据 → PR 3 通过后我贴的「PR 3 验证」截图里**还有深色侧栏**，用户立即指出「首页项目列表 按照原型是 没有左侧菜单的」。**
**适用 → Epic 150 后续 X2/X3 视图重构（workspace topbar/heading、8 视图 heading）。**

### 2. 隐藏条件用排除清单而非白名单

修复：app.html:30/196 各加 `view() !== 'home'` 进排除条件。

错误做法（白名单）：`@if (view() === 'project' || view() === 'epic' || ...)` — 任何新 view 都要改。
正确做法（排除清单）：`@if (view() !== 'document' && view() !== 'home' && view() !== 'project' && ...)` — 新 view 默认显示，更稳健。

但当前 app.html 已经是排除清单写法，所以只是补 home。

### 3. E2E 测脚本依赖 sidebar 入口，但 home view 没有 sidebar

最初 E2E 测脚本用 `page.locator("a.sidebar-nav-item:has-text('项目')")` 切 projects view。
PR 3 follow-up 后 home view 隐藏 sidebar → click 失败。

**正确做法**：
- 跨 view 切用 `page.goto(FRONTEND_ORIGIN + path)`（token 在 localStorage 还在，Angular 重新读）
- 同一 view 内切 tab 用真实 click（agents 视图是 `goAgents()` 直接 set view，不是路由 → 不能 goto `/agents`）

最终 E2E 步骤编排：
- step 1: goto / (home)
- step 2: goto /projects (切到 projects)
- step 3: click sidebar Agents (在 projects view 用 sidebar 切 agents)
- step 4: goto / (回 home)

### 4. `/agents` 不是路由，是 `view.set('agents')`

`app.ts:2888` 的 `goAgents()` 是直接 `this.view.set('agents')`，没有对应路由配置。
所以 `page.goto("/agents")` 不会触发 agents view，会被 Angular Router 重定向到默认路由（home）。

**规则 → 切 view 之前先看 app.ts 怎么实现的：路由跳转 vs 直接 set view signal。**

### 5. 验证 home view 与原型对齐要看实际渲染图，不光看 class

原 home view 渲染图（修复前）显示有双 topbar（外层浅色 + home-shell 深色）+ 深色 sidebar。
外层 topbar 里的「企业 项目协作平台」+ 通知/头像，与 home-shell 内部的「搜索 + 通知 + 头像」重复。

修复后：只剩 home-shell 自己的 topbar（logo + 项目/Agents nav + 搜索 + 通知 + 头像），与原型 1:1 对齐。

## 验证记录

- `tests/e2e_epic149/test_x1_pr3_route_switch.py` 4 步全 PASS
- Step 1（home default）: home-shell 渲染、11 monogram、outer shell 隐藏
- Step 2（home → projects）: home-shell 消失、outer shell 显示（回归）
- Step 3（projects → agents via sidebar）: home-shell 仍消失
- Step 4（agents → home）: home-shell 重新挂载、outer shell 隐藏

## 下一步

- Task 1292（X1 dev task）→ 标 done
- Story 322 (X1) → 标 done
- 创建 Task 1293（X2 dev）：workspace topbar + workspace-heading 框架
- 创建 Task 1294（X3 dev）：8 视图 heading 改造
- 重要：X2/X3 也要先看 prototype 03/04/05 截图，确认外层 sidebar/topbar 在 workspace 视图里的行为是否符合预期
