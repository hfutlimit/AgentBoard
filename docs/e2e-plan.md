# AgentBoard e2e 计划与进度

> 配套 `tests/e2e/dod_registry.py` — 每完成一个 e2e 阶段,在两处同步更新。
> 维护规则:阶段完成 = 1) e2e test 跑过 2) 单测不被破坏 3) 文档就位 4) commit + push。

最后更新:2026-08-21

---

## 1-13. (历史归档 — 见 `docs/project-context/refactor-progress.md`)

历史重构线 (后端 9 阶段 / Epic 149 前端拆 tab / 双栈 BFF 演进 / 仓库清理) 的 e2e 状态已在 refactor-progress 中追踪。本文件只追踪 **2026-08-21 起的结构性调整 + 后续新功能**。

---

## 14. 项目工作台多 Tab 系统 (2026-08-21)

**目标**:把 8 个子视图(概览/看板/Epics/工作项/提案/文档/成员/设置)从「单 slot 切换」升级为「浏览器风格多 tab 同时挂载」。用户可在 Kanban tab 输入筛选/滚动,切到 Proposal tab 改东西,再切回 Kanban 一切如旧。

**v2 修**:tab 切换是纯 client state 操作(ajax 风格),**不**触发 Angular router 跳路由(否则会调 app.ts loadRoute 重拉数据,用户感知为"刷新 + 状态丢失")。

**约束**:
- 顶部 topbar 必须完整保留(用户红线)
- 8 个 menu 项 aria-label 全部存在(向后兼容 e2e_epic149/test_x_b1_route_8tab)
- 不持久化 tab 列表(刷新清空 tab,但 URL 仍可恢复当前激活 tab)
- 切项目 → tab 列表清空
- 同 (projectId, kind) 至多 1 个 tab
- **切 tab 不应触发整页刷新 / 数据重拉** (v2 修)

### 进度表

| # | 阶段 | 状态 | 关联 commit | DoD 链接 |
|---|---|---|---|---|
| 1 | WorkspaceTabsService 设计 + 实现 | ✅ done | (本 commit) | `tests/e2e/dod_registry.py::epic152-workspace-tabs-2026-08-21` |
| 2 | TabPaneComponent 派发器 | ✅ done | (本 commit) | 同上 |
| 3 | ProjectWorkspaceShellComponent 重构 (sidebar + tab strip + pane stack) | ✅ done | (本 commit) | 同上 |
| 4 | app.routes.ts 8 child section routes 保留 (直链/刷新用) + SectionPlaceholderComponent | ✅ done | (本 commit) | 同上 |
| 5 | **v2 修**: 菜单/tab 条用 `(click) + service + history.replaceState`,**不**触发 router 跳路由 | ✅ done | (v2 commit) | 同上 |
| 6 | Playwright e2e 7 test_* 真实断言 (含 v2 修的 no_reload + state_preserved + url_replaced) | ✅ done | (v2 commit) | 同上 |
| 7 | Angular 单测 70/70 通过 + Build 0 error | ✅ done | (v2 commit) | 同上 |
| 8 | Dev API + 真实浏览器验证 | 🟡 待 dev 栈跑 | — | 同上 |

### 验收 (7 e2e test_* 函数,详见 `tests/e2e_workspace_tabs/test_workspace_tabs_e2e.py`)

| test_* | 验什么 | 期望 |
|---|---|---|
| `test_open_project_default_overview_tab_only` | 进入项目默认态 | 1 tab(概览)+ 概览激活 |
| `test_click_menu_adds_tab` | 点菜单新增 tab | 点 Kanban → 2 tab;再点 Proposals → 3 tab |
| `test_click_existing_tab_activates_only` | 点已开 tab | tab 数不变,只切换激活态 |
| `test_close_tab_activates_neighbor` | 关闭中间非激活 tab | tab 数 -1,原激活态保持 |
| `test_no_page_reload_on_tab_click` (v2 修) | 切 tab 不刷新页面 | DOM sentinel 切 3 次后仍 = 'present' |
| `test_state_preserved_across_tab_switch` (v2 修) | 切走再切回,组件状态保留 | documents tab select 数量切前后一致 |
| `test_url_replaced_silently_on_tab_click` (v2 修) | URL 用 replaceState 静默同步 | URL 更新但 history.length 不变 |

### v1 → v2 修复根因

- **v1** 菜单/tab 条用 `<a routerLink>` → 点击触发 Angular router 跳路由
  → `app.ts` 的 `routeSub` 订阅 `NavigationEnd` 调 `loadRoute()`
  → `loadRoute()` 重新拉项目数据 / tab 数据 → 用户感知为"页面刷新"
  → 同时 `loadRoute` 内部 `setProject` 路径会触发 host signal 重发 → effect 重跑 → 状态被覆盖
- **v2** 菜单/tab 条用 `(click)` 直接调 `tabsService.openTab/activateTab`
  + `history.replaceState` 静默同步 URL(不触发 Angular router)
  → 不再 `loadRoute` → 数据不重拉 → 切 tab 是纯 ajax 风格 → 其他 tab 状态完整保留
- **直链/刷新** 仍然 work:`/project/123/kanban` 直链 → 路由匹配 → shell 挂载
  → 构造函数读 URL → 调 `openTab` → service 状态对齐 URL
- **浏览器前进/后退**:`popstate` 事件 → 重新读 URL → 调 `openTab` → service 同步

### 后续 backlog (本 commit 不做)

- Tab 顺序拖拽排序
- Tab 持久化 (localStorage)
- Story/Task/Epic 详情页也走 tab (当前为占位 route)
- 移动端 ( < 840px ) tab 条折叠为下拉
