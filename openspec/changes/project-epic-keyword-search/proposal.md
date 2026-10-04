# Proposal: 项目 Epic 列表关键词搜索与清空刷新

## Why

项目工作台的 Epic 列表已有本地状态筛选和稳定的“创建时间倒序、同时间按 ID
倒序”排序，但它仍通过宿主通用 `search` 状态调用 `match()`。这会让其他页面的
搜索条件影响 Epic 页，也没有 Epic 专属输入、清空动作或清空后的服务端刷新路径。
用户既不能可靠地按标题或描述即时收敛当前项目的 Epic，也无法在清除条件后确认
列表已重新从服务端取得最新数据。

## What Changes

1. 在 `App` 中新增不持久化的 `epicSearchQuery`，仅用于项目工作台 Epic 列表；
   `visibleEpics` 不再读取通用 `search`。
2. Epic 派生结果按“规范化关键词匹配标题/描述 → 精确状态筛选 → 既有稳定排序”
   的顺序计算。关键词和状态筛选取交集，关键词或状态变化都会将 Epic 分页回到第 1 页。
3. `EpicsTabComponent` 保持无状态：接收关键词值，发出关键词变化和清空事件；仅在
   规范化后关键词非空时呈现可操作的清空按钮。
4. 清空动作只清除 Epic 关键词、不清除状态筛选、复位页码，并通过既有
   `loadProjectTab('epics', projectId, true)` 路径重新调用 `listEpics(projectId)`。
   现有 loading、error 和 retry 投影继续负责请求状态。
5. 切换项目时同时清空 Epic 关键词与状态筛选；路由工作台和标签页工作台的两个
   `app-epics-tab` 装配点传递完全相同的新输入/输出合同。
6. 让所有会写入共享 Epic 快照的同项目请求可区分：`overview` 聚合加载与 `epics`
   Tab 强制刷新均须使用同一快照代次；由该快照触发且尚未完成的进度异步链也必须随之
   失效。这样先前请求、旧项目请求以及先前进度链均不能覆盖较新的当前项目结果。
7. 将 `openProposalModal()` 取得的 Epic 候选改为提案弹窗专用状态，而不是写入共享
   `epics`；该请求和弹窗初始化使用项目代次加弹窗加载代次保护。项目切换或较新的
   弹窗打开操作后，旧响应不得覆盖当前 Epic 列表、显示旧错误，或以旧项目初始化/打开弹窗。

## Non-goals

- 不新增 Epic 搜索 API、查询参数、后端排序、数据库迁移、缓存或持久化状态。
- 不改变全局命令面板、首页项目搜索、Epic 详情页、状态值/状态筛选选项、进度计算、
  新建/详情导航，或既有 `created_at DESC, id DESC` 排序规则。
- 不在 `EpicsTabComponent` 注入 API 服务、发起请求或自行管理加载/错误状态。
- 不实现该功能；本变更只建立实现前的 OpenSpec 工件和可验证合同。

## Impact

- `src/frontend/src/app/app.ts`：Epic 专属 signal、派生列表、分页/项目切换复位、
  清空强制刷新，以及覆盖 `overview`、`epics` 和进度链的共享快照/Tab 状态请求判定；
  提案弹窗的专用 Epic 候选与其项目/弹窗代次保护。
- `src/frontend/src/app/app.html`（或当前承载新建提案弹窗的模板）：将目标 Epic 下拉来源
  切换到提案弹窗专用候选，避免再消费共享 `epics`。
- `src/frontend/src/app/epics-tab/`：关键词输入、条件清空按钮和输入/输出契约。
- `src/frontend/src/app/project-workspace-route/` 与
  `src/frontend/src/app/project-workspace-shell/tab-pane/`：两个装配入口的新绑定。
- `src/frontend/src/app/app.spec.ts`、`epics-tab.spec.ts` 及必要的入口模板测试：
  覆盖派生、刷新、共享请求防覆盖、提案弹窗竞态和绑定回归。

后端、REST/MCP 合同、数据库、部署产物及当前工作树中的无关修改均不在范围内。
