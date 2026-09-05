# Proposal: 项目 Epic 列表默认排序与状态筛选

## Why

项目工作台的 `Epics` 页已经从当前项目加载 Epic，并经 `App.visibleEpics()`
传给 `EpicsTabComponent`。但该派生列表目前只沿用通用文本匹配：没有状态筛选，
也没有明确的创建时间排序契约。用户在项目中创建新 Epic 后，不能可靠地先看到最新
内容；当列表有多页时，也无法把结果收敛到一个状态。

## What Changes

1. `Epics` 工具栏提供状态单选下拉：默认“全部状态”，以及“待办、进行中、评审中、完成、已阻塞”。
2. 派生列表先按选择的状态过滤，再按 `created_at` 倒序、`id` 倒序稳定排序。
3. 切换状态时将 Epic 分页复位到第 1 页；分页总数和空态以筛选、排序后的完整结果集计算。
4. 建立可自动验证的派生数据、交互和回归验收用例。

## Non-goals

- 不新增或变更后端 API 查询参数、服务端排序、数据库迁移或数据回填。
- 不新增关键字搜索、排序字段选择、升降序切换或本地持久化。
- 不改变 Epic 状态值、状态机或状态流转；本变更只读取 `Epic.status`。
- 不改变 Epic 进度、详情链接、新建入口、加载/失败重试或其他项目 Tab 的行为。

## Status baseline and compatibility boundary

| 层 | 当前事实 | 本变更的处理 |
| --- | --- | --- |
| 后端 `Epic` 数据库约束 | `backlog`、`todo`、`in_progress`、`in_review`、`verifying`、`done`；默认 `backlog` | 不修改。|
| 前端 `Status` 类型 | `backlog`、`todo`、`in_progress`、`in_review`、`done`、`blocked`；不含 `verifying` | 不借此顺带修改共享类型。|
| 工作台 `statuses` 与 `statusLabel` | 仅暴露/翻译 `todo`、`in_progress`、`in_review`、`done`、`blocked` | 用作本 Story 指定的筛选选项。|

因此，本次 UI 契约明确为题目所列的五个业务筛选值加“全部状态”。历史或服务端返回的
`backlog` / `verifying` 值在“全部状态”下必须仍可见，且保持本变更的排序；它们不因
本次筛选功能被改写、隐藏或新增状态流转。若产品要求分别筛选这两个值，必须先以独立
状态对齐变更统一后端约束、前端类型、文案和 badge，不能在本 Story 中静默扩张选项。

## Impact

- `src/frontend/src/app/app.ts`：新增 Epic 筛选状态与不原地修改数据的派生计算。
- `src/frontend/src/app/epics-tab/`：增加筛选输入/输出及工具栏下拉。
- `src/frontend/src/app/project-workspace-route/` 与
  `src/frontend/src/app/project-workspace-shell/tab-pane/`：两处现存 `app-epics-tab`
  装配点都接入同一契约，避免路由和 shell 渲染不一致。
- `src/frontend/src/app/app.spec.ts`（及必要的组件渲染测试）：增加聚焦自动化覆盖。

后端、MCP、数据库、部署静态产物均不在本变更范围内。
