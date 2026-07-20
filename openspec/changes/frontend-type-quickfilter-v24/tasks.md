# 任务清单：前端体验升级 v2.4 — 任务类型快速筛选 chips

> 纯前端增量，复用既有 `filterTypes` 信号与 `visibleTasks()` 过滤逻辑，不改动后端契约。

## 任务
- [x] **T1（high）任务类型快速筛选 chips** —— 已实现并经 Playwright E2E 全绿验证，Task 865 置 in_review。
  - `app.ts`：`filterTypes` 初始化读 `localStorage.agentboard_quick_type`；新增 `typeCounts` computed；新增 `setQuickType(t)` 单选切换 + `persistQuickType()` 持久化；`clearFilters()` 联动重置并 `persistQuickType()`。
  - `app.html`：状态 chips 后追加第三个 `task-quickfilter-bar`（全部 + 任务 + Bug 带计数），复用 `.qf-chip`/`.qf-count`。
  - `app.css`：复用既有样式，无需新增类。
  - 新增代码约 30 行（远低于范围红线）。

## 验证
- [ ] Playwright E2E：类型 chips 渲染（3 枚）、点「Bug」→仅 bug、「任务」→仅 task、「全部」→还原、reload 持久化、0 pageerror/console/.js+.css 404。
- [ ] 回归：Epic v2.0/v2.3/v2.5/mine-filter/v1.9 E2E + `pytest test_epic30_cache.py` 全绿。

## 状态
- [x] T1 → backlog → todo → in_progress → in_review（Epic 34 / Story 74 / Task 865 同步 in_review）
