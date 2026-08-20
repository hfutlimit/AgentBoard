# Epic 151 / Task 1315 半路由化深化 — 调研与执行计划

**日期**：2026-08-20
**状态**：调研完成 + 部分执行（本轮未完成全部 @if 块替换为 router-outlet，scope 超单次 commit）
**关联 review**：Epic 149 静态 Review 高优先级 #4（路由化未彻底）

## 现状

```text
app.html
├── @switch (view()) {
│   @case ('home') { <app-home-shell> }
│   @case ('project') {
│     <app-workspace-topbar>
│     <app-workspace-heading>
│     @if (activeTab() === 'overview') { <app-overview-tab> }
│     @if (activeTab() === 'epics')     { <app-epics-tab> }
│     @if (activeTab() === 'backlog')   { <app-backlog-tab> }
│     @if (activeTab() === 'settings')  { <app-settings-tab> }
│     @if (activeTab() === 'proposals') { <app-proposals-tab> }
│     @if (activeTab() === 'members')   { <app-members-tab> }
│     @if (activeTab() === 'documents') { <app-documents-tab> }
│     @if (activeTab() === 'kanban')    { <app-kanban-tab> }
│   }
│   ...
│ }
```

8 个 @if 块都在 `app.html` 顶层 `@switch` 内部，**没**用 router-outlet 渲染。

`app.routes.ts` 已 `loadComponent` 化 8 tab，但 `app.html` 顶层没有 `<router-outlet>`，所以 `loadComponent` 工厂创建的实例**没被渲染**。`ng build` 时 Webpack 仍然按 lazy chunk 切，但 chunk 只包含 loadComponent 工厂 + 几行 metadata（导致 chunk 100-200 字节）。

## 根因

`app.html` 顶层在 Story 327（半路由化）改造时**主动删了**根 `<router-outlet />`（行 4063 注释："避免与 @if 渲染双轨"）。当时为了不破坏现有 `@Input` 数据流，先用 hybrid 模式（URL + routerLink + activeTab signal 驱动 @if 渲染），约定「拆 router-outlet」留到 Story 328+。

但 Story 328/329 都没推进这块。

## 完整 Task 1315 执行方案

### 阶段 1：app.html 拆 @if 块为 router-outlet

```html
@switch (view()) {
  @case ('home') { <app-home-shell> }
  @default {       <!-- 包括 'project' / 'agents' / 'projects' / 独立 tab 等 -->
    <router-outlet></router-outlet>
  }
}
```

### 阶段 2：8 tab 组件自管 ActivatedRoute + 数据加载

8 tab 当前通过 `@Input` 接收 `project` / `epics` / `backlogCount` 等数据（来自 app.ts signal）：

```typescript
// 当前（app.html 顶层）
<app-overview-tab
  [project]="project()"
  [members]="projectMembers()"
  [epics]="visibleEpics()"
  ... />

// 改后（overview-tab.ts 自管）
@Inject(ActivatedRoute) private route: ActivatedRoute;
ngOnInit() {
  this.route.paramMap.pipe(takeUntilDestroyed()).subscribe(p => {
    const id = Number(p.get('id'));
    this.api.getProject(id).subscribe(...);
    this.api.listMembers(id).subscribe(...);
    ...
  });
}
```

### 阶段 3：app.ts imports 数组删除 8 tab

```typescript
// 删：
import { OverviewTabComponent } from './overview-tab/overview-tab';
import { KanbanTabComponent } from './kanban-tab/kanban-tab';
... (8 个)

imports: [
  ...,
  // OverviewTabComponent, KanbanTabComponent, ... 全部删除
  FocusTrapDirective, BottomTabBarComponent,  // 保留
]
```

`ng build --configuration=production` 后：
- main bundle 估 < 800 KB（从 1.12 MB 降 ~30%）
- 8 tab lazy chunk 估 5-30 KB（每个含真组件模板 + 样式）

### 阶段 4：SettingsTabComponent 升级为真实现

`SettingsTabComponent` 当前是 1.9 KB 占位组件。需要把 `app.html` 内的 350 行 settings @if 块内容迁入。

## 风险

1. **数据流变化**：8 tab 当前拿 @Input，改后自管 ActivatedRoute。需重写每个 tab 组件的 ngOnInit。**8 个文件 4-8 小时工作量**。
2. **焦点 / 滚动位置**：router-outlet 替换 @if 会触发 Angular 重新渲染模板结构，可能影响 focus / scroll restoration。
3. **回归风险**：hybrid 模式跑通 x_b1 / x_b2 / x_a1 / x_a2 / vitest 13/13 + 4/4，改成纯路由可能破坏。
4. **Signal 与 RxJS 桥接**：8 tab 用 signal 还是 Observable？需要选型。

## 本轮执行

- ✅ 完成调研 + 文档
- ⏳ 阶段 1-3 留待后续 Story（推荐 Story 331「前端 god component 拆分第一刀：8 tab 拆 lazy route」，并入 Epic 145 P1 架构）
- ✅ vitest 69 passed / 4 E2E PASS / build 0 warning（Story 329 其余 task 完成）

## 关联

- Story 327（已 done，hybrid 模式）
- Epic 145 P1 架构（blocked/后续）「前端 god component 拆分」
- Task 1321 build budget（已 done 但仍依赖 Task 1315 真正降包）
