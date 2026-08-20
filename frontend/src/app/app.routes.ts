/**
 * AgentBoard global routes.
 *
 * Project workspace navigation is a real parent/child route tree. The router
 * owns the active tab; App only remains the transitional data/action host.
 */
import { Component } from '@angular/core';
import type { Routes } from '@angular/router';

/** 占位组件：用于暂未路由化的 path（保持向后兼容，由 app.ts loadRoute 接管）。 */
@Component({ selector: 'app-route-anchor', template: '' })
class RouteAnchor {}

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
  {
    path: 'documents',
    loadComponent: () =>
      import('./documents-tab/documents-tab').then(
        (m) => m.DocumentsTabComponent,
      ),
  },
  {
    path: 'documents/:id',
    loadComponent: () =>
      import('./documents-tab/documents-tab').then(
        (m) => m.DocumentsTabComponent,
      ),
  },
  {
    path: 'proposals',
    loadComponent: () =>
      import('./proposals-tab/proposals-tab').then(
        (m) => m.ProposalsTabComponent,
      ),
  },
  {
    path: 'proposals/:id',
    loadComponent: () =>
      import('./proposals-tab/proposals-tab').then(
        (m) => m.ProposalsTabComponent,
      ),
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
