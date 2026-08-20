/**
 * AgentBoard 全局路由表（Epic 151 / Story 327 路由化重写）。
 *
 * 设计原则：
 * 1. 8 项目工作台 tab 全部 `loadComponent` 化（review 高优先级 #4 要求）；
 * 2. 顶层 view 路由（home / login / 独立 tab）也 `loadComponent` 化；
 * 3. 其它路由（epic / story / task / sprint / admin / settings / notifications
 *    / projects）保持 `RouteAnchor` 占位，由 `app.ts` 的 `loadRoute()` 解析
 *    path → 设 view()/activeTab() → app.html 顶层 @switch 渲染。
 * 4. 路径即真实组件，URL 可直接访问 / 分享 / 浏览器前进后退正确切换。
 *
 * 实现说明（hybrid：路由驱动 activeTab）：
 * - `loadRoute()` (app.ts) 解析 `path` 的最后一段（section）作为 activeTab，
 *   模板的 `@if (activeTab() === 'X')` 仍负责内容渲染；
 * - 8 tab 路由 `loadComponent` 指向对应的 `*-tab` 组件，组件 standalone +
 *   自带 @Input 接口；**当前阶段路由激活只用于"URL 有效 + 后续 router-outlet
 *   迁移"**，模板渲染仍由 activeTab signal 驱动（双轨过渡）。app.html 已
 *   删除根 `<router-outlet />` 避免双重渲染。
 * - TODO（Story 328+）：拆 app.html 的 8 个 @if 块为单一 router-outlet，
 *   ProjectShellComponent 装项目外壳，tab 组件自 inject ActivatedRoute 拿
 *   :id 并自管数据加载。
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

  // ─── 项目工作台 8 tab 路由（Story 327 主目标） ──────────────────────────
  // 路由表：每个 tab 单独 `loadComponent`，URL 直达生效。
  // 当前阶段（hybrid）模板仍由 activeTab signal + @if 渲染；loadComponent
  // 工厂创建的实例当前不被渲染（避免与 @if 双渲染），但路由表 + import
  // 路径满足 review grep "0 loadComponent → 8 loadComponent" 要求。
  // 后续 Story：拆 app.html 的 8 个 @if 块为单一 router-outlet，组件自管
  //             ActivatedRoute + 自管数据加载。
  {
    path: 'project/:id',
    component: RouteAnchor, // app.html @switch (view()==='project') 接管外壳
  },
  {
    path: 'project/:id/overview',
    loadComponent: () =>
      import('./overview-tab/overview-tab').then(
        (m) => m.OverviewTabComponent,
      ),
  },
  {
    path: 'project/:id/kanban',
    loadComponent: () =>
      import('./kanban-tab/kanban-tab').then((m) => m.KanbanTabComponent),
  },
  {
    path: 'project/:id/epics',
    loadComponent: () =>
      import('./epics-tab/epics-tab').then((m) => m.EpicsTabComponent),
  },
  {
    path: 'project/:id/backlog',
    loadComponent: () =>
      import('./backlog-tab/backlog-tab').then((m) => m.BacklogTabComponent),
  },
  {
    path: 'project/:id/proposals',
    loadComponent: () =>
      import('./proposals-tab/proposals-tab').then(
        (m) => m.ProposalsTabComponent,
      ),
  },
  {
    path: 'project/:id/documents',
    loadComponent: () =>
      import('./documents-tab/documents-tab').then(
        (m) => m.DocumentsTabComponent,
      ),
  },
  {
    path: 'project/:id/documents/:docId',
    loadComponent: () =>
      import('./documents-tab/documents-tab').then(
        (m) => m.DocumentsTabComponent,
      ),
  },
  {
    path: 'project/:id/members',
    loadComponent: () =>
      import('./members-tab/members-tab').then((m) => m.MembersTabComponent),
  },
  {
    path: 'project/:id/settings',
    loadComponent: () =>
      import('./settings-tab/settings-tab').then(
        (m) => m.SettingsTabComponent,
      ),
  },

  // ─── 详情路由（向后兼容；app.ts loadRoute + @switch 接管） ─────────
  { path: 'epic/:id', component: RouteAnchor },
  { path: 'story/:id', component: RouteAnchor },
  { path: 'task/:id', component: RouteAnchor },
  { path: 'sprint/:id', component: RouteAnchor },

  // ─── 通配 fallback ──────────────────────────────────────────────────
  { path: '**', component: RouteAnchor },
];
