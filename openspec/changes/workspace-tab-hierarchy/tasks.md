# Tasks: 项目工作台 Tab 层级化与工作上下文

## 实现

- [x] 扩展 `WorkspaceTab`：`level` / `accent` / `parentId` / `parentTitle` / `pinned` / `visitedAt`，并为 `TAB_META` 补齐每个 kind 的层级与色标。
- [x] 实现 `displayTabs` computed：固定置顶 + 父子聚拢 + 孤儿缩进，输出带 `depth` / `grouped` 的 `WorkspaceTabView`。
- [x] `openEntityTab()` 增加 `parent` 参数；新增 `setParent()`，供详情加载完成后回填父子关系。
- [x] 新增 `togglePin()` / `closeAll()` 跳过固定项 / `closeOthers()` 保留固定项；Pin 按项目持久化到 `agentboard_ws_pins_<projectId>`。
- [x] 新增最近访问（`recent`，上限 10 条，仅记录实体 Tab），持久化到 `agentboard_ws_recent_<projectId>`，切项目时按项目载入。
- [x] Tab 条渲染：左侧类型色条、子 Tab 缩进、父级前缀、`title`/`aria-label` 输出完整路径、Pin 按钮。
- [x] Tab 条右侧新增「最近」下拉与「更多」菜单（关闭其他 / 全部关闭 / 切换 Task 点击行为）。
- [x] 新增 `WorkspaceDrawerService` 与 `WorkspaceDrawerComponent`：Task 默认 Drawer，支持「在 Tab 中打开」、Esc / 遮罩关闭、宽度可拖拽并记忆。
- [x] `App.openWorkspaceEntity()` 增加 `asTab` 选项，按 `workspaceDrawer.taskPrefersDrawer()` 分流；Task 直链/刷新时同样进 Drawer。
- [x] 未修改任何后端 API、模型、状态机、数据库迁移或 MCP 工具。

## 自动化验证

- [x] `workspace-tabs.service.spec.ts` 新增：父子分组与缩进深度、子先于父打开时的归位、孤儿缩进、`setParent` 回填、level/accent 元信息。
- [x] `workspace-tabs.service.spec.ts` 新增：固定置顶、全部关闭保留固定、关闭其他保留固定与目标、Pin 按项目隔离。
- [x] `workspace-tabs.service.spec.ts` 新增：最近访问记录与直链路径、重新激活置顶去重、Section 不进最近、切项目后恢复。
- [x] 在 `src/frontend` 运行 `ng test --runner-config vitest.config.ts`：8 个文件 105 passed / 1 skipped。
- [x] 在 `src/frontend` 运行 `ng build`：编译通过（仅存量 bundle budget warning）。

## 验收标准

- [x] 打开 1 个 Epic + 若干 Story / Task 后，子 Tab 缩进显示在所属父 Tab 之后，并带类型色标与图标。
- [x] Tab 的 `title` / `aria-label` 包含完整父级路径。
- [x] backlog / Story 详情中点击 Task 打开右侧 Drawer，Tab 条不新增条目；点「在 Tab 中打开」后才新增 Task Tab。
- [x] 固定 Tab 置顶且不被批量关闭清掉；Pin 与最近访问刷新后保留，且按项目隔离。
- [x] 最近访问下拉展示最近 10 条（含类型与父级路径），点击可重新打开。
- [x] `/project/3/tasks/123` 直链与刷新仍可打开对应 Task。
- [ ] 浏览器端人工验证：Tab 条在浅色/深色主题下的可读性、Drawer 拖拽宽度、窄屏下 Tab 条不溢出。
