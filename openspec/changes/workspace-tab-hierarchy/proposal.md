# Proposal: 项目工作台 Tab 层级化与工作上下文

## Why

项目工作台的 Tab 条目前是「浏览器页面模型」：8 个 Section Tab 与 Epic / Proposal / Story / Task 四种实体 Tab 全部平铺在同一行、同一视觉层级。用户实际看到的是 `概览 | 工作项 | Task: QA | Task: 实现 | Story: Worker | Task: 设计 | Story: 文档` 这样一串没有结构的标签，由此产生三类问题：

1. **粒度失控**：Task 与 Section 同级，打开 6 个 Task 就产生 6 个一级 Tab，Tab 条迅速溢出，用户无法判断「哪个是最新状态」。
2. **层级丢失**：Tab 上看不出某个 Task 属于哪个 Epic、某个 Story 挂在哪，与用户脑中的 `Project → Epic → Story → Task` 结构不一致，操作几分钟后就会失去方位感。
3. **没有工作上下文**：关掉 Tab 后无法恢复之前的工作上下文；没有固定、没有最近访问，跨会话的连续性为零。

根因不是「有没有 Tab」，而是 **Tab 的语义是页面而不是工作上下文**。

## What Changes

- `WorkspaceTab` 增加层级元信息：`level`（section / epic / proposal / story / task）、`accent`、`parentId`、`parentTitle`、`pinned`、`visitedAt`。
- Tab 条按类型着色（Epic 蓝 / Story 绿 / Task 黄 / 提案 紫 / 视图 灰）并缩进成组：子 Tab 聚拢到所属父 Tab 之后，即使子 Tab 比父 Tab 先打开也会归位；父 Tab 未打开时子 Tab 仍保留一级缩进，避免看起来像顶层。
- Tab 的 `title` 与 `aria-label` 携带完整父级路径（`Epic 31 › Story 文档快速定位 › Task QA`），收窄时也不丢上下文。
- 新增固定（Pin）：固定项置顶，且不被「关闭其他 / 全部关闭」清掉；Pin 状态按项目持久化到 localStorage。
- 新增「最近访问」下拉：记录最近 10 个实体访问（含父级路径与直链），刷新后可恢复；按项目隔离。
- 新增右侧 Drawer：Task 默认在 Drawer 打开，不占一级 Tab；Drawer 头部提供「在 Tab 中打开」把当前实体提升为常驻 Tab。Epic / Story / Proposal 保持 Tab 行为不变。
- Task 点击行为可在 Tab 条「更多」菜单切换（Drawer / 新 Tab），偏好持久化。

## Non-goals

- 不改后端契约：本次纯前端，不新增或修改任何 REST 端点。
- 不改路由模型：保留 `/project/:id/:section/:entityId` 直链与刷新恢复能力，本阶段不引入 `/project/:id/workspace` 新路由。
- 不做 VS Code 式左侧层级树（Tree Sidebar）与底部 Agent Activity Panel —— 属于第二阶段。
- 不改动 Section Tab 的内容渲染逻辑。

## Impact

- `src/frontend/src/app/services/workspace-tabs.service.ts`：层级元信息、`displayTabs` 分组排序、Pin、最近访问、localStorage 持久化。
- `src/frontend/src/app/services/workspace-drawer.service.ts`（新增）：Drawer 开关与宽度状态。
- `src/frontend/src/app/project-workspace-shell/`：Tab 条层级渲染、右侧下拉菜单、父子关系绑定 effect、Drawer 宿主。
- `src/frontend/src/app/project-workspace-shell/workspace-drawer/`（新增）：Drawer 组件。
- `src/frontend/src/app/app.ts`：`openWorkspaceEntity` 支持 `asTab` 选项并按偏好分流到 Drawer。
- `src/frontend/src/app/services/workspace-tabs.service.spec.ts`：新增分组、Pin、最近访问用例。

后端、MCP、数据库、部署产物均不在本变更范围内。
