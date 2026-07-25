# Tasks: 快速查看抽屉任务前后导航（v4.5）

## 实现任务清单

- [x] T1 前端逻辑：`app.ts` 新增 `qvHasPrev()` / `qvHasNext()` / `qvNav(delta)`（基于 `visibleTasks()` 当前索引，越界早退）；`openQuickView` 切换任务时重置 `qvEditingTitle` / `qvEditingDesc`；新增 `onDrawerKeydown(event)`（`[` 上一项 / `]` 下一项，输入框聚焦不触发）。
- [x] T2 模板：`app.html` 抽屉头部新增 `.qv-nav-group`（上一项 `‹` / 下一项 `›`，边界 `disabled`），`<aside>` 绑定 `(document:keydown)="onDrawerKeydown($event)"`。
- [x] T3 样式：`app.css` 补齐 `.qv-nav-group` / `.qv-nav`（含 dark 主题），复用 `.qv-close` 视觉语言。
- [x] T4 构建与部署：`npm run build`（managed node 22.22.2，清 `.angular/cache`）→ cp `dist/frontend/browser/.` → `agentboard/web/static/`，删除旧 `main-*.js`。
- [x] T5 验证：Playwright `tests/test_epic58_v45_drawer_nav_e2e.py` 全绿（0 pageerror/console/.js+.css 404）；回归 `pytest test_epic30_cache.py`（8 passed）；抽屉评论渲染回归核对（见下方说明）。
- [x] T6 状态流转：Task 1108 / Story 97 / Epic 48 经 `backlog→todo→in_progress→in_review` 合法链置 **in_review**。

## 追踪实体（REST 兜底，MCP 连接器断开）
- project 43 (AUTO58) → epic 48 (Epic 58 v4.5) → story 97 (Story 58.1) → task 1108 (high)

## 回归说明（重要）
- 本次新增的 v4.5 抽屉导航 E2E 全绿，零控制台/页面/资源错误。
- 既有 `test_epic30_cache.py` 8 passed，无后端回归。
- 关于 v4.4 抽屉评论区 E2E（`test_epic57_v44_drawer_comments_e2e.py`）：其「UI 添加评论后抽屉计数 == 2」断言在本次环境（本地 dev 库 58125 + 自建 8090 web）下失败，但经对照实验确认——**在还原本次改动后的原始代码上同样失败**（抽屉显示 1、服务端已 2），属既有抽屉评论列表刷新瑕疵，与 v4.5 导航改动无关（导航改动不触及评论信号）。该既有测试默认指向 Docker 18000/28080，本次本地验证未改动其目标，故不在本次交付范围内修复。
