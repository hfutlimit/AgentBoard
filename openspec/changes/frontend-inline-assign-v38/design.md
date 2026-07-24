# Design: 任务列表行内快速指派 (v3.8 / Epic 51)

## 概述
在单体 `App` 组件（`frontend/src/app/`）内，复用 v3.4 行内快速状态切换的浮层模式，新增一套对称的「行内快速指派」交互。

## 复用资产
- `app.ts`
  - `members: signal<ProjectMember[]>` —— 项目成员列表（批量指派面板同款数据源）
  - `loadMembers(projectId)` —— 懒加载成员
  - `api.updateTask(id, { assignee_id })` —— 既有改派端点（行 1769 已用于取消指派）
  - `getAssigneeName(user_id)` / `getAssigneeInitials(user_id)` —— 成员展示
  - `statusMenuTaskId` / `statusMenuPos` / `openStatusMenu` / `closeStatusMenu` —— v3.4 浮层模式（直接镜像）
- `app.html`
  - `entity-item-meta` 内指派人头像（行 1418-1422，位于 `<a routerLink>` 内，需 `preventDefault` 防跳转，与状态徽章同处理）
  - `.status-menu--fixed` / `.status-menu-backdrop` 浮层样式（直接复用）
- `app.css`：组件作用域，规则编译进 `main-*.js`

## 新增组件状态（app.ts）
```ts
readonly assignMenuTaskId = signal<number | null>(null);
readonly assignMenuPos = signal<{ x: number; y: number } | null>(null);
assignMenuTask(): Task | undefined { /* 在 tasks() 中按 id 查找 */ }
async openAssignMenu(task: Task, event: Event): Promise<void> {
  event.stopPropagation(); event.preventDefault();
  if (this.members().length === 0 && task.project_id) await this.loadMembers(task.project_id);
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  this.assignMenuTaskId.set(task.id);
  this.assignMenuPos.set({ x: rect.left, y: rect.bottom + 4 });
}
closeAssignMenu(): void { this.assignMenuTaskId.set(null); this.assignMenuPos.set(null); }
async quickAssign(task: Task, userId: number | null): Promise<void> {
  this.closeAssignMenu();
  if (task.assignee_id === userId) return;
  await firstValueFrom(this.api.updateTask(task.id, { assignee_id: userId && userId > 0 ? userId : null }));
  this.tasks.update(list => list.map(t => t.id === task.id ? { ...t, assignee_id: userId && userId > 0 ? userId : null } : t));
  this.notify(`已将「${task.title}」指派给「${userId ? this.getAssigneeName(userId) : '未指派'}」`);
}
```

## 模板变更（app.html）
1. 指派人头像外层包 `<span class="assignee-pill" role="button" tabindex="0" (click)="openAssignMenu(...)" (keydown.enter)="openAssignMenu(...)">`，内部保留 `.assignee-avatar-sm` 渲染（已指派 / 未指派两种态）。
2. 在 v3.4 状态浮层 `@if` 块之后新增对称 `assignMenu` 浮层：`@if (assignMenuTaskId() !== null && assignMenuTask())`，内含 `.status-menu-backdrop` + `.status-menu--fixed.assign-menu`，遍历 `members()` 渲染成员项（头像 + 姓名，`active` 高亮当前指派），末尾「未指派」项。

## 样式（app.css）
- `.assignee-pill { cursor: pointer; }` + hover 轮廓提示可点击
- `.status-menu-item.active`（指派菜单当前选中态）高亮背景 + 头像描边

## 构建 / 部署
`npm run build`（managed node 22.22.2，必要时清 `.angular/cache`）→ 产物 `frontend/dist/frontend/browser/` → `cp` 至 `agentboard/web/static/`（web 28080 直读静态，即时生效），删旧 `main-*.js`。

## 验证
- Playwright E2E `tests/test_epic51_v38_inline_assign_e2e.py`：登录 admin → /story/N → 点击某任务指派人头像 → 浮层列出成员 → 点击某成员 → 行内指派人即时更新（经 API 复核）→ 「未指派」项可取消 → 0 pageerror / console / .js+.css 404。
- 回归：现有 pytest（缓存） + v3.4/v3.5/v3.6/v3.7 E2E 全绿。
