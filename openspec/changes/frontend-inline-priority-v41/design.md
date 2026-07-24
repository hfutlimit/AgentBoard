# Design: 任务列表行内快速修改优先级 (v4.1 / Epic 54)

## 概述
在单体 `App` 组件（`frontend/src/app/`）内，复用 v3.4 行内快速状态切换的浮层模式，新增一套对称的「行内快速修改优先级」交互。优先级是本地枚举（无成员依赖），故比 v3.8 指派更简单。

## 复用资产
- `app.ts`
  - `priorities: Priority[] = ['highest','high','medium','low','lowest']`（行 309）—— 浮层数据源
  - `priorityLabel(priority)`（行 3015）—— 档位中文文案
  - `api.updateTask(id, { priority })` —— 既有改优先级端点（v2.9 bulk 已用）
  - `tasks` 信号 —— 即时局部刷新
  - `statusMenuTaskId` / `statusMenuPos` / `openStatusMenu` / `closeStatusMenu` —— v3.4 浮层模式（直接镜像）
- `app.html`
  - `entity-item-meta` 内优先级徽章（行 1399-1401，位于 `<a routerLink>` 内，需 `preventDefault` 防跳转，与状态徽章同处理）
  - `.status-menu--fixed` / `.status-menu-backdrop` 浮层样式（直接复用）
- `app.css`：组件作用域，规则编译进 `main-*.js`

## 新增组件状态（app.ts）
```ts
// v4.1: 任务列表行内快速修改优先级（与 v3.4 状态 / v3.8 指派 / v3.9 截止日期 对称）
readonly priorityMenuTaskId = signal<number | null>(null);
readonly priorityMenuPos = signal<{ x: number; y: number } | null>(null);
priorityMenuTask(): Task | undefined {
  const id = this.priorityMenuTaskId();
  return id == null ? undefined : this.tasks().find((t) => t.id === id);
}
openPriorityMenu(task: Task, event: Event): void {
  event.stopPropagation(); event.preventDefault();
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  this.priorityMenuTaskId.set(task.id);
  this.priorityMenuPos.set({ x: rect.left, y: rect.bottom + 4 });
}
closePriorityMenu(): void { this.priorityMenuTaskId.set(null); this.priorityMenuPos.set(null); }
async quickSetPriority(task: Task, newPriority: string): Promise<void> {
  this.closePriorityMenu();
  if (task.priority === newPriority) return;
  try {
    await firstValueFrom(this.api.updateTask(task.id, { priority: newPriority }));
    this.tasks.update(list => list.map(t => t.id === task.id ? { ...t, priority: newPriority as Priority } : t));
    this.notify(`已将「${task.title}」优先级改为「${this.priorityLabel(newPriority)}」`);
  } catch {
    this.notify('更新优先级失败，请重试', 'error');
  }
}
```

## 模板变更（app.html）
1. 优先级徽章（行 1399-1401）包裹为可点击 `.priority-pill`（`role=button tabindex=0`，`preventDefault` 防跳转，键盘可达，镜像状态徽章），加 `▾` caret 与 title 提示。
2. 在 v3.9 截止日期浮层 `@if` 块之后新增对称 `priorityMenu` 浮层：`@if (priorityMenuTaskId() !== null && priorityMenuTask())`，内含 `.status-menu-backdrop` + `.status-menu--fixed.priority-menu`，遍历 `priorities` 渲染档位项（色点 + 文案，`active` 高亮当前优先级）。

## 样式（app.css）
- `.priority-pill { cursor: pointer; }` + hover 轮廓提示可点击（镜像 `.status-pill`）
- `.priority-dot` 小圆点，复用 `.priority--{p}` 背景色
- `.status-menu-item.active`（优先级菜单当前选中态）高亮背景

## 构建 / 部署
`npm run build`（managed node 22.22.2，必要时清 `.angular/cache`）→ 产物 `frontend/dist/frontend/browser/` → `cp` 至 `agentboard/web/static/`（web 28080 直读静态，即时生效），删旧 `main-*.js`。

## 验证
- Playwright E2E `tests/test_epic54_v41_inline_priority_e2e.py`：登录 admin → /story/N → 点击某任务优先级徽章 → 浮层列出 5 档 → 点击某档 → 行内优先级即时更新（经 API 复核 `priority` 变更）→ 遮罩关闭 / 即时更新 / 0 pageerror / console / .js+.css 404。
- 回归：现有 pytest（缓存） + v3.4/v3.8/v3.9 E2E 全绿。
