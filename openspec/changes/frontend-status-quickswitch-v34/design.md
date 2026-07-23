# Design: 任务列表行内快速状态切换 (v3.4 / Epic 47)

## 现有可复用资产
- `api.setTaskStatus(id, status)`（`api.service.ts:480`）—— 任务详情 / 看板拖拽 / `toggleTaskComplete` 均已使用
- `statuses: Status[]`、`statusLabel()`、`statusColor()`（`app.ts` 295 / 2956 / 2982）已全局可用
- `tasks = signal<Task[]>`（`onKanbanDrop` 中通过 `this.tasks.update(...)` 局部刷新状态，证明该模式有效）
- 现有 `.status-pill` 可由 `.badge.status.status--{s}` 直接派生（原状态徽章即此结构）

## 方案
1. **状态机镜像**：`app.ts` 新增 `readonly statusTransitions: Record<string,string[]>` 与前端点对点一致；`validNextStatuses(task)` 返回合法目标数组。
2. **浮层状态**：`statusMenuTaskId` / `statusMenuPos` 信号记录当前打开的行与浮层坐标；`openStatusMenu(task, event)` 用 `getBoundingClientRect()` 计算 fixed 定位；`closeStatusMenu()` 复位。
3. **交互**：`quickSetStatus(task, target)` 调 `api.setTaskStatus` 后 **显式 `this.tasks.update(...)`** 局部刷新（关键：早期漏掉该刷新导致徽章不更新），并 `notify` 反馈。
4. **模板**：
   - 任务行状态徽章改为 `<span class="status-pill" (click)="openStatusMenu(item,$event);stopPropagation();preventDefault()">`，`role=button` + `tabindex=0` + 键盘 Enter 可达。
   - 行外（`@for(grp)` 之后、列表视图内）渲染**单次** fixed 浮层 `.status-menu` + 透明遮罩 `.status-menu-backdrop`；菜单项 `@for(st of validNextStatuses(statusMenuTask()!))` 带状态色点。
   - fixed 定位 + `(statusMenuPos()?.x ?? 0)` 空安全绑定，规避滚动容器 `overflow` 裁剪。
5. **样式**：`app.css` 新增 `.status-pill`（hover 微动效 + caret）、`.status-menu--fixed`、`.status-menu-item`、`.status-dot`、`.status-menu-backdrop`，含 dark 主题 hover。

## 关键实现坑（已解决）
- **信号未刷新**：`quickSetStatus` 必须像 `onKanbanDrop` 那样 `this.tasks.update(list => list.map(...))`，否则后台已改、行内徽章不更新。
- **类型**：`status` 字段为 `Status` 类型，更新时 `{...t, status: target as Status}` 避免 TS 字面量 widened 报错。
- **裁剪**：任务列表容器可滚动，`position:absolute` 会被裁剪，改用 `position:fixed` + 坐标计算。
- **导航误触**：状态徽章位于 `<a class="entity-item-link">` 内，`stopPropagation` + `preventDefault` 阻断 routerLink 跳转。
