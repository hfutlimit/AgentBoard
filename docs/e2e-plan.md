# AgentBoard e2e 计划与进度

> 配套 `tests/e2e/dod_registry.py` — 每完成一个 e2e 阶段,在两处同步更新。
> 维护规则:阶段完成 = 1) e2e test 跑过 2) 单测不被破坏 3) 文档就位 4) commit + push。

最后更新:2026-08-21 19:30 (v7.3 任务列表简化 + delete cascade bugfix 收尾)

---

## 1-13. (历史归档 — 见 `docs/project-context/refactor-progress.md`)

历史重构线 (后端 9 阶段 / Epic 149 前端拆 tab / 双栈 BFF 演进 / 仓库清理) 的 e2e 状态已在 refactor-progress 中追踪。本文件只追踪 **2026-08-21 起的结构性调整 + 后续新功能**。

---

## 14. 项目工作台多 Tab 系统 (2026-08-21)

**目标**:把 8 个子视图(概览/看板/Epics/工作项/提案/文档/成员/设置)从「单 slot 切换」升级为「浏览器风格多 tab 同时挂载」。用户可在 Kanban tab 输入筛选/滚动,切到 Proposal tab 改东西,再切回 Kanban 一切如旧。

**v2 修**:tab 切换是纯 client state 操作(ajax 风格),**不**触发 Angular router 跳路由(否则会调 app.ts loadRoute 重拉数据,用户感知为"刷新 + 状态丢失")。

**v3 修 (Step 1)**:*-tab 内部 link (Story/Task/Epic/Proposal/Sprint/Document) 改为 master-detail side panel,**不**跳顶层 /story/:id / /task/:id / /epic/:id 全页。Step 1 是占位 panel (kind + id + 关闭 + open full page 链接),Step 2 提取 app.html @case 内容到独立 component 替换占位。

**约束**:
- 顶部 topbar 必须完整保留(用户红线)
- 8 个 menu 项 aria-label 全部存在(向后兼容 e2e_epic149/test_x_b1_route_8tab)
- 不持久化 tab 列表(刷新清空 tab,但 URL 仍可恢复当前激活 tab)
- 切项目 → tab 列表清空
- 同 (projectId, kind) 至多 1 个 tab
- **切 tab 不应触发整页刷新 / 数据重拉** (v2 修)
- **从 *-tab 内部点 link 不应跳顶层全页** (v3 修)

### 进度表

| # | 阶段 | 状态 | 关联 commit | DoD 链接 |
|---|---|---|---|---|
| 1 | WorkspaceTabsService 设计 + 实现 | ✅ done | 636298c | `tests/e2e/dod_registry.py::epic152-workspace-tabs-2026-08-21` |
| 2 | TabPaneComponent 派发器 | ✅ done | 636298c | 同上 |
| 3 | ProjectWorkspaceShellComponent 重构 (sidebar + tab strip + pane stack) | ✅ done | 636298c | 同上 |
| 4 | app.routes.ts 8 child section routes 保留 (直链/刷新用) + SectionPlaceholderComponent | ✅ done | 636298c | 同上 |
| 5 | **v2 修**: 菜单/tab 条用 `(click) + service + history.replaceState`,**不**触发 router 跳路由 | ✅ done | 1a259db | 同上 |
| 6 | **v3 修 (Step 1)**: DetailPaneComponent + workspace 内 click 拦截 + side panel | 🗑️ deprecated | (v3 commit) | — (v3 - 4 修替换为「新浏览器 tab 打开」) |
| 6' | **v3 - 4 修**: 用户拒绝 side panel → *-tab 内部点 link 直接 `window.open(href, '_blank', 'noopener,noreferrer')` 开新 tab，workspace 上下文不变 | ✅ done | (v3 - 4 commit) | `tests/e2e/dod_registry.py::epic152-detail-new-tab-2026-08-21` |
| 7 | Playwright e2e 7 (v2) + 4 (v3 - 4) test_* 真实断言 | ✅ done | (v3 - 4 commit) | 同上 |
| 8 | Angular 单测 70/70 通过 + Build 0 error | ✅ done | (v3 - 4 commit) | 同上 |
| 9 | ~~v3 修 (Step 2)~~: side panel 真实详情渲染 — **v3 - 4 修后不再需要**（顶层 /story/:id 等全页路由直接在新 tab 打开） | ✅ done | (v3 - 4 commit) | — |
| 10 | Dev API + 真实浏览器验证 | ✅ done (28080/18000) | (v3 - 4 commit) | — |

### 验收 (7 + 5 e2e test_* 函数)

**v2 (test_workspace_tabs_e2e.py)**:

| test_* | 验什么 | 期望 |
|---|---|---|
| `test_open_project_default_overview_tab_only` | 进入项目默认态 | 1 tab(概览)+ 概览激活 |
| `test_click_menu_adds_tab` | 点菜单新增 tab | 点 Kanban → 2 tab;再点 Proposals → 3 tab |
| `test_click_existing_tab_activates_only` | 点已开 tab | tab 数不变,只切换激活态 |
| `test_close_tab_activates_neighbor` | 关闭中间非激活 tab | tab 数 -1,原激活态保持 |
| `test_no_page_reload_on_tab_click` (v2 修) | 切 tab 不刷新页面 | DOM sentinel 切 3 次后仍 = 'present' |
| `test_state_preserved_across_tab_switch` (v2 修) | 切走再切回,组件状态保留 | documents tab select 数量切前后一致 |
| `test_url_replaced_silently_on_tab_click` (v2 修) | URL 用 replaceState 静默同步 | URL 更新但 history.length 不变 |

**v3 (test_detail_pane_e2e.py)**:

| test_* | 验什么 | 期望 |
|---|---|---|
| `test_detail_pane_appears_on_internal_link_click` (v3 修) | 点 *-tab 内部 link → side panel | panel 出现,URL 不跳顶层,workspace tab 上下文保留 |
| `test_detail_pane_closes_on_x` (v3 修) | 点 × 关闭 panel | panel 消失,workspace 上下文不变 |
| `test_detail_pane_does_not_break_workspace_context` (v3 修) | panel 打开时切 tab 仍 work | 切 tab 不关 panel,无 page reload |
| `test_detail_pane_does_not_affect_other_links` (v3 修) | 侧栏菜单 link 不被误伤 | 切 tab 正常工作 |
| `test_detail_pane_open_full_page_works` (v3 修) | "open in full page" 跳顶层 | panel 关 + URL = /epic/:id |

### v1 → v2 修复根因

- **v1** 菜单/tab 条用 `<a routerLink>` → 点击触发 Angular router 跳路由
  → `app.ts` 的 `routeSub` 订阅 `NavigationEnd` 调 `loadRoute()`
  → `loadRoute()` 重新拉项目数据 / tab 数据 → 用户感知为"页面刷新"
- **v2** 菜单/tab 条用 `(click)` 直接调 `tabsService.openTab/activateTab`
  + `history.replaceState` 静默同步 URL(不触发 Angular router)
  → 不再 `loadRoute` → 数据不重拉 → 切 tab 是纯 ajax 风格

### v2 → v3 修复根因

- **v2** 后,从 *-tab 内部点 Story/Task/Epic/Proposal/Sprint 链接仍会跳到顶层全页路由
  (`/story/:id` / `/task/:id` / `/epic/:id` / `/proposals/:id` / `/sprint/:id`)
  → app.ts 切到 'story'/'epic'/'task' view → 整个 app 退出 workspace 上下文
- **v3** 在 workspace 内部加 click 拦截器:捕获指向这 6 类 detail 路由的 `<a>` click
  → preventDefault + 显示 master-detail side panel(workspace main 右侧滑出)
  → 顶层 /story/:id 全页路由仍 work(从命令面板/通知/URL bar 进入的场景)

### 后续 backlog (本 commit 不做)

- ~~**v3 Step 2**: 提取 app.html @case ('story' / 'task' / 'epic' / 'proposal' / 'sprint') 到独立 component,side panel 用真实详情渲染~~ — **v3 - 4 修后废弃**（顶层 /story/:id 等全页路由直接在新 tab 打开，workspace 上下文不被污染）
- Tab 顺序拖拽排序
- Tab 持久化 (localStorage)
- 移动端 ( < 840px ) tab 条折叠为下拉

---

## 15. Story 详情页任务列表简化 v7.3 (2026-08-21)

**目标**:用户原话「task 列表里 task 不会那么多 重新设计下 简洁一点」。把 Story 详情页
「Task 列表」tab 的旧 4 行 taskbar + 11 个 chip + 8 个 export 菜单项的繁复 UI
收敛到 1 行 taskbar + 收纳到 popover + 零计数隐藏 + 行内降噪。

**核心变化**:
- **taskbar 单行**:n/m + 进度条 fill 在同一行,不再 4 行堆叠
- **选项 popover**:点 `.icon-btn[aria-label='Task 选项']` → 6 个控件(只看我 / 密度 / 排序 /
  分组 / 筛选预设 inline / 导出 CSV/JSON) 在 popover 内,不再摊在 taskbar 上
- **零计数 chip 隐藏**:5 个状态 chip 中计数为 0 的不渲染(评审中 / 已阻塞常为 0)
- **行内降噪**:task 行无 due 不渲染「无截止」占位 pill,无 assignee 不渲染「未分配」
  占位 pill,无 due 时不显示「设截止」inline 编辑文案
- **icon-btn 标签默认隐藏**:只有 focus/hover 显,kbd 提示 focus 显 (kbmode)
- **filterbar--inline**:筛选预设条横置,不再浮动
- **状态/进度数据走 service**:statusCounts / completedCounts / totalCounts 全部中央
  helper,保证 zero-count 隐藏的精确性

**约束**:
- Story 详情页默认 tab 是「详情」,e2e 必须先切「Task 列表」tab
- API 事实 = 页面事实:计数断言必须用 `GET /api/stories/{id}/tasks` 读回,
  适配 `create_story` 自动编排生成「设计：/开发：」2 个子任务
- 不再支持旧 `.task-list-summary` 结构(全替换为 `.taskbar--slim`)
- 不再支持旧 `.export-menu`(移除,只有单个 `.icon-btn[aria-label='导出']`)

### 进度表

| # | 阶段 | 状态 | 关联 commit | DoD 链接 |
|---|---|---|---|---|
| 1 | app.ts: `taskOptionsOpen` / `taskOptionsActive` / `toggle` / `close` + 清理 `presetOpen` 死代码 | ✅ done | (v7.3 commit) | `tests/e2e/dod_registry.py::v73-story-slim-tasks-2026-08-21` |
| 2 | app.html: 重写 Story Task 列表区(taskbar 精简 + 选项 popover + chips 隐藏零计数 + 行内降噪) | ✅ done | (v7.3 commit) | 同上 |
| 3 | app-features.css: `.taskbar--slim` / `.task-opts-popover` / `.icon-btn` / `.hover-reveal` / `.kbd-hint` focus 显隐 | ✅ done | (v7.3 commit) | 同上 |
| 4 | app.spec.ts: 更新 story task controls 断言到新结构 | ✅ done | (v7.3 commit) | 同上 |
| 5 | e2e_story_slim_tasks 6 个 test_* 真实断言 | ✅ done (6/6, 35s) | (v7.3 commit) | 同上 |
| 6 | Angular 单测 70/70 通过 + Build 0 error | ✅ done | (v7.3 commit) | 同上 |
| 7 | **bugfix**: DELETE /api/epics\|/api/stories 500 修复 + e2e teardown 零残留 | ✅ done | (v7.3-bugfix commit) | `tests/e2e/dod_registry.py::v73-bugfix-delete-cascade-fk-2026-08-21` |
| 8 | 注册 dod_registry + 更新 e2e-plan.md §14-16 + README Status | ✅ done | (v7.3 commit) | — |
| 9 | commit + push to main | ✅ done | (v7.3 commit) | — |

### 验收 (6 e2e test_* 函数 + 70 Angular 单测)

**`tests/e2e_story_slim_tasks/test_story_slim_tasks_e2e.py`**:

| test_* | 验什么 | 期望 |
|---|---|---|
| `test_taskbar_slim_structure` | Task 列表 tab 渲染新 taskbar | 单行 `.taskbar--slim`，内联进度条，无 `.task-list-summary`，icon-btn 标签 display:none |
| `test_task_options_popover_opens_with_all_controls` | 点 Task 选项 icon-btn | `.task-opts-popover` 开合 + 6 个控件全在 + 无 `.export-menu` |
| `test_zero_count_chips_hidden` | statusCounts 零计数 chip 隐藏 | 5 个状态最多 4 个 chip，评审中/已阻塞零计数不渲染 |
| `test_task_row_inline_noise_reduction` | 行内降噪 | 无 due 无 assignee 不渲染占位 pill、无「设截止」文案 |
| `test_density_change_persists_after_popover_close` | 密度切换持久 | popover 内切密度→关闭后 `.task-opts-dot` 仍显示活动点 |
| `test_default_state_noise_reduction` | 默认态降噪 | task-checkbox 默认 opacity:0、kbd-hint 默认 display:none |

**回归保护**:
- `tests/test_delete_cascade_fk.py` 7 个 case 覆盖 delete_epic/delete_story/delete_task
  的 NO ACTION FK 防御级联(task_outcome / episode_embedding / project_playbook* /
  ReviewVote.comment_id / agent_schedules.epic_id)

---

## 16. DELETE /api/epics|/api/stories 500 Bug 修复 (2026-08-21)

**目标**:v7.3 e2e 收尾时发现的产品级 bug —— task 走 done(落 learning outcome)
后再删 epic/story → SQLite 抛 `FOREIGN KEY constraint failed` → HTTP 500。
e2e teardown 全部静默失败，dev 库残留 18 个 v73-e2e epic + 7 个 fk-probe epic。

**根因**:Epic 140 切片 1/3 引入 `task_outcome` / `episode_embedding` /
`project_playbook*` / `project_playbook_episode` 后(FK → tasks.id，**NO ACTION**)，
旧 `delete_epic`(:499) / `delete_story`(:829) 走裸批量 delete(只清 Comment+Task+
Story+Epic)绕过了中央 `delete_task`(:1032)的防御性级联。

**修复策略**:
1. `delete_epic` / `delete_story` 改为**逐 task 调中央 `delete_task`**，单实现多入口
2. `delete_epic` 同步**解绑 `agent_schedules.epic_id`**(NO ACTION 置 NULL 保留 schedule)
3. `delete_epic` / `delete_story` 同步**切断 `ReviewVote` story 锚点**(entity_id 置 -1)
4. 中央 `delete_task` 同步补 **`ReviewVote.comment_id` 防御**(删 task comment 前
   先 NULL 化 vote.comment_id，防 NO ACTION FK 撞)

**回归覆盖**:
- `tests/test_delete_cascade_fk.py` 7 个 test_* 覆盖 4 类 NO ACTION FK + 2 个边界
- 25 个 dev 库垃圾 epic 全部 DELETE 200 清空(回归 0 残留)
- v7.3 e2e 6/6 跑完 teardown 零残留(全链路 200)

**已知未覆盖**(本 commit 不做):
- `delete_project` 同类洞(`project_playbook.project_id` / `project_playbook_episode.project_id`
  也 NO ACTION)，待后续 follow-up。当前测试基础设施不支持跨 project 的 full test client 走通，
  风险评估为低 — 项目删除走确认页 + 二次提示，存量 dev 数据无 task_outcome 关联的 project 删除场景。

---

## 17. Worker 统一执行层 Stage 0 · 止血与韧性收敛 (2026-08-26)

**目标**: 针对 Worker 执行层当前存在的 5 类核心韧性缺陷进行原地止血修复，不引入新的平行抽象，为后续 Stage 1-3 统一执行管线与编排上移奠定数据与通信底座。

**核心变化**:
- **Story/Task 认领租约与超期回收**:
  - `stories` 与 `tasks` 表增加 `claimed_by` / `claimed_at` 租约列及 `(status, claimed_at)` 复合索引（迁移 `a9b8c7d6e5f4`）；
  - `claim_story` 与 `claim_development_task` 成功认领时写入当前 Worker 身份与租约时间戳；
  - 开放 `POST /api/stories/reclaim-stale` 与 `POST /api/tasks/reclaim-stale` 端点；
  - `ProposalWorker` 与 `WorkflowConsumer` 在维护循环与启动期自动触发超期租约回收，根治 Worker 崩溃后任务卡死问题。
- **多 Agent 路由键对齐与容错**:
  - `RoutedSubprocessInvoker` 白名单对齐实际 action（`review_task` / `process_task` / `process_story` 等）；
  - 历史近似别名（`review` → `review_task`, `story` → `process_story`, `task` → `process_task`）自动归一化；
  - 未知路由键记录 Warning 警告并跳过，避免配置笔误导致静默失配。
- **子进程环境安全隔离**:
  - `SubprocessAgentInvoker` 启动子进程时主动剥离 `AGENTBOARD_*` 凭据族环境变量，防止 Worker Token 泄漏给子 Agent CLI；
  - 强制注入 `PYTHONIOENCODING=utf-8` 与 `PYTHONUTF8=1`，解决 Windows 平台多语言与编码解析异常。
- **MQ 瞬时异常退避与三态重投**:
  - 引入 `MessageRetry` 异常与 `(ack, dead, retry)` 三态判定；
  - 网络抖动与服务端 5xx 等瞬时错误触发指数退避并在 Broker 端 `requeue` 重投，达到最大重试次数后才进入死信队列。
- **通用工作项异步执行与去重**:
  - `AsyncWorkExecutor` 从仅支持 Story 泛化为统一管理 `clarify` / `ticket` / `story` 域；
  - 引入 `(kind, id)` in-flight 去重，防止长任务在后台执行期间主循环重复拉取堆积。

### 进度表

| # | 阶段 | 状态 | 关联 commit | DoD 链接 |
|---|---|---|---|---|
| 1 | Story / Task 模型与 Alembic 迁移（`claimed_by` / `claimed_at`） | ✅ done | (Stage 0 commit) | `tests/e2e/dod_registry.py::stage0-worker-resilience-2026-08-26` |
| 2 | POST /api/stories/reclaim-stale 与 POST /api/tasks/reclaim-stale 端点实现 | ✅ done | (Stage 0 commit) | 同上 |
| 3 | Worker 维护循环接入 Story/Task 租约回收与启动期探测 | ✅ done | (Stage 0 commit) | 同上 |
| 4 | RoutedSubprocessInvoker 路由白名单对齐与历史别名归一化 | ✅ done | (Stage 0 commit) | 同上 |
| 5 | SubprocessAgentInvoker 子进程环境隔离与 UTF-8 注入 | ✅ done | (Stage 0 commit) | 同上 |
| 6 | MQ 消费端 MessageRetry 三态判定与指数退避重投 | ✅ done | (Stage 0 commit) | 同上 |
| 7 | AsyncWorkExecutor 通用化与 (kind, id) in-flight 去重 | ✅ done | (Stage 0 commit) | 同上 |
| 8 | 单元测试与回归套件验证（202 passed / 0 failed） | ✅ done | (Stage 0 commit) | 同上 |
| 9 | 注册 dod_registry + 更新 e2e-plan.md + commit & push | ✅ done | (Stage 0 commit) | 同上 |

---

## 18. Worker 统一执行层 Stage 1 & 2 · 统一执行抽象与 Server 编排收缴 (2026-08-26)

**目标**: 彻底解决 Proposal / Task / Review 流程与进程割裂问题。解耦业务领域模型（保留 Proposal/Task/Story 实体特性）与底层执行抽象（统一为 `WorkType` 与 `ExecutionCommand`），收敛多进程为单一常驻 `WorkerCoordinator`，并将跨实体 DAG 推演与结项判定全面收缴至 Server 状态机。

**核心变化**:
- **统一执行抽象契约 (`contract.py`)**:
  - `WorkType`: `proposal_clarify` / `proposal_convert` / `task_implement` / `task_review` / `task_respond`；
  - `ExecutionCommand`: 包含 `execution_id`, `work_type`, `entity_type`, `entity_id`, `attempt`, `context`, `lease_token`；
  - `ExecutionResult`: 结构化上报 `status`, `action`, `summary`, `inspected_files`, `output`, `error_message`。
- **单一协调器中枢 (`WorkerCoordinator`)**:
  - 统管 `HandlerRegistry[WorkType]`，提供全局单入口 `dispatch(command)`；
  - 线程安全 `(work_type, entity_id)` in-flight 去重，杜绝重复并发执行；
  - 聚合全域轮询扫描（`poll_once`）与 MQ 事件流（`handle_workflow_message`）。
- **Handler 执行策略收敛 (`handlers/`)**:
  - 5 个 Handler (`ClarifyHandler`, `TicketHandler`, `StoryHandler`, `ReviewHandler`, `OwnerResponseHandler`) 统一继承 `BaseWorkHandler`；
  - 移除 Handler 内部的 `complete_story` / `_story_all_tasks_done` / `_story_fail` 等硬编码，实现为纯粹的 `execute_command()` 策略类。
- **服务端统一编排与自动收尾**:
  - Task 评审 Approve 并进入 `DONE` 时，Server 端自动扫描所属 Story 下的所有任务：若全部已 `DONE`，Server 自动触发 `complete_story` 结项；
  - 修复 `delete_epic` / `delete_story` 在删除评论前先解绑 `ReviewVote.comment_id`，防止 SQLite NO ACTION FK 约束报错。

### 进度表

| # | 阶段 | 状态 | 关联 commit | DoD 链接 |
|---|---|---|---|---|
| 1 | 定义统一契约 `WorkType` / `ExecutionCommand` / `ExecutionResult` (`contract.py`) | ✅ done | (Stage 1-2 commit) | `tests/e2e/dod_registry.py::stage1-2-worker-unified-execution-2026-08-26` |
| 2 | 更新 `handlers/base.py` 引入 `BaseWorkHandler` 策略基类 | ✅ done | (Stage 1-2 commit) | 同上 |
| 3 | 收敛 5 个 Handler (Clarify, Ticket, Story, Review, OwnerResponse) 实现 `execute_command` | ✅ done | (Stage 1-2 commit) | 同上 |
| 4 | 实现单一进程协调器 `WorkerCoordinator` (`coordinator.py`) 与全局派发入口 | ✅ done | (Stage 1-2 commit) | 同上 |
| 5 | Server 端实现 Task 评审通过后自动检查 Story 下全任务完成并自动结项 | ✅ done | (Stage 1-2 commit) | 同上 |
| 6 | 修复 `delete_epic` / `delete_story` FK 删除顺序防御 | ✅ done | (Stage 1-2 commit) | 同上 |
| 7 | 编写 `tests/unit/test_worker_coordinator.py` 并通过全量 209 单测 + 35 集成测试 | ✅ done | (Stage 1-2 commit) | 同上 |
| 8 | 注册 DoD 记录 + 更新 e2e-plan.md + commit & push | ✅ done | (Stage 1-2 commit) | 同上 |

