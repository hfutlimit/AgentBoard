/**
 * AgentBoard global routes.
 *
 * Project workspace navigation is a real parent/child route tree. The router
 * owns the active tab; App only remains the transitional data/action host.
 */
import { Component, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import type { Routes } from '@angular/router';
import { WorkspaceHeadingComponent } from './workspace-heading/workspace-heading';

/**
 * 占位组件：详情路由与 404 fallback 的统一展示（Epic 152 路由收口过渡）。
 *
 * - 详情路由 /story/:id /task/:id /epic/:id /sprint/:id：显示实体类型+编号+回退指引
 * - 通配 ** fallback：显示 404 页面不存在
 * - 顶层 view（projects/notifications/admin/settings/agents）：现由 app.html @switch 接管，
 *   RouteAnchor 在这些路由下不会被实际渲染（保留兼容）
 */
@Component({
  selector: 'app-route-anchor',
  standalone: true,
  imports: [RouterLink, WorkspaceHeadingComponent],
  template: `
    <app-workspace-heading
      [eyebrow]="eyebrow()"
      [title]="title()"
      [subtitle]="subtitle()">
    </app-workspace-heading>
    <div class="empty-state-v7 empty-inline-v7 route-anchor-card">
      <p class="route-anchor-hint">{{ message() }}</p>
      <p class="muted route-anchor-tip">临时方案：在命令面板 (Ctrl+K) 搜索，或从工作台内对应卡片进入。</p>
      <a routerLink="/" class="btn-primary-sm route-anchor-back">返回首页</a>
    </div>
  `,
  styles: [`
    .route-anchor-card { max-width: 560px; margin: 32px auto; padding: 28px 24px; }
    .route-anchor-hint { color: var(--text); font-size: 14px; font-weight: 600; margin: 0 0 6px; }
    .route-anchor-tip { margin: 0 0 12px; font-size: 12px; }
    .route-anchor-back { display: inline-flex; align-items: center; }
  `],
})
class RouteAnchor {
  readonly url = signal('');
  readonly eyebrow = signal('占位');
  readonly title = signal('正在加载中…');
  readonly subtitle = signal('Epic 152 路由收口过渡');
  readonly message = signal('该视图正在迁移中。');

  constructor(private router: Router) {
    const path = this.router.url;
    this.url.set(path);
    const m = path.match(/^\/(story|task|epic|sprint)\/(\d+)/);
    if (m) {
      const typeMap: Record<string, string> = { story: 'Story', task: 'Task', epic: 'Epic', sprint: 'Sprint' };
      this.eyebrow.set('详情占位');
      this.title.set(`${typeMap[m[1]]} #${m[2]}`);
      this.subtitle.set('详情页正在 Epic 152 路由收口迁移（Story 332c）');
      this.message.set(`该 ${typeMap[m[1]]} 详情页路由尚未完成迁移。`);
    } else {
      this.eyebrow.set('404');
      this.title.set('页面不存在');
      this.subtitle.set(`访问的路由 ${path} 未定义`);
      this.message.set('该路由未在 app.routes.ts 中定义。');
    }
  }
}

export const routes: Routes = [
  // ─── 顶层 view 路由 ──────────────────────────────────────────────────
  {
    path: '',
    loadComponent: () =>
      import('./home-shell/home-shell').then((m) => m.HomeShellComponent),
    pathMatch: 'full',
  },
  {
    path: 'login',
    loadComponent: () => import('./login/login').then((m) => m.LoginComponent),
  },
  { path: 'projects', component: RouteAnchor }, // app.html @switch 接管
  { path: 'notifications', component: RouteAnchor },
  { path: 'admin', component: RouteAnchor },
  { path: 'settings', component: RouteAnchor },
  { path: 'agents', component: RouteAnchor },  // Story 329 / Task 1322: 真正的 router link，app.ts loadRoute 走 'agents' view

  // ─── 独立 tab 路由（顶层 view，无项目上下文） ─────────────────────────
  // #1428 修复：全局 /documents /proposals 路由通过 data.scope = 'global' 区分
  // 与项目工作区（/project/:id/documents）的 project 模式。
  {
    path: 'documents',
    data: { scope: 'global' },
    loadComponent: () =>
      import('./documents-tab/documents-tab').then(
        (m) => m.DocumentsTabComponent,
      ),
  },
  {
    path: 'documents/:id',
    data: { scope: 'global' },
    loadComponent: () =>
      import('./documents-tab/documents-tab').then(
        (m) => m.DocumentsTabComponent,
      ),
  },
  {
    path: 'proposals',
    data: { scope: 'global' },
    loadComponent: () =>
      import('./proposals-tab/proposals-tab').then(
        (m) => m.ProposalsTabComponent,
      ),
  },
  {
    path: 'proposals/:id',
    data: { scope: 'global' },
    loadComponent: () =>
      import('./proposals-tab/proposals-tab').then(
        (m) => m.ProposalsTabComponent,
      ),
  },

  // ─── Story 348 #1430：全局聚合视图路由（无项目上下文） ───────────────
  // 5 个路由共用 GlobalStatsTabComponent，靠 @Input entity 切标题 / 高亮。
  // 全局 list endpoint 待后端补（GET /api/epics|stories|tasks 需 project_id/epic_id/story_id），
  // 当前只展示 /api/overview 聚合 + 跳转卡。详见组件注释。
  {
    path: 'epics',
    loadComponent: () =>
      import('./global-stats-tab/global-stats-tab').then(
        (m) => m.GlobalStatsTabComponent,
      ),
    data: { entity: 'epics' },
  },
  {
    path: 'stories',
    loadComponent: () =>
      import('./global-stats-tab/global-stats-tab').then(
        (m) => m.GlobalStatsTabComponent,
      ),
    data: { entity: 'stories' },
  },
  {
    path: 'tasks',
    loadComponent: () =>
      import('./global-stats-tab/global-stats-tab').then(
        (m) => m.GlobalStatsTabComponent,
      ),
    data: { entity: 'tasks' },
  },
  {
    path: 'bugs',
    loadComponent: () =>
      import('./global-stats-tab/global-stats-tab').then(
        (m) => m.GlobalStatsTabComponent,
      ),
    data: { entity: 'bugs' },
  },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./global-stats-tab/global-stats-tab').then(
        (m) => m.GlobalStatsTabComponent,
      ),
    data: { entity: 'dashboard' },
  },

  // ─── Project workspace: shell + eight lazy child routes ───────────────
  {
    path: 'project/:id',
    loadComponent: () =>
      import('./project-workspace-shell/project-workspace-shell').then(
        (m) => m.ProjectWorkspaceShellComponent,
      ),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      ...['overview', 'kanban', 'epics', 'backlog', 'proposals', 'documents', 'members', 'settings'].map(
        (path) => ({
          path,
          loadComponent: () =>
            import('./project-workspace-route/project-workspace-route').then(
              (m) => m.ProjectWorkspaceRouteComponent,
            ),
        }),
      ),
      {
        path: 'documents/:docId',
        loadComponent: () =>
          import('./project-workspace-route/project-workspace-route').then(
            (m) => m.ProjectWorkspaceRouteComponent,
          ),
      },
    ],
  },

  // ─── 详情路由（向后兼容；app.ts loadRoute + @switch 接管） ─────────
  { path: 'epic/:id', component: RouteAnchor },
  { path: 'story/:id', component: RouteAnchor },
  { path: 'task/:id', component: RouteAnchor },
  { path: 'sprint/:id', component: RouteAnchor },

  // ─── 通配 fallback ──────────────────────────────────────────────────
  { path: '**', component: RouteAnchor },
];
