import { Component, ViewEncapsulation } from '@angular/core';

/**
 * SectionPlaceholderComponent — 2026-08-21 结构调整
 *
 * 项目工作台 8 个子 section（overview/kanban/.../settings）路由的占位组件。
 *
 * 设计：父路由 ProjectWorkspaceShellComponent 不再用 <router-outlet> 渲染
 * child — 它订阅 Router 事件，从 URL 解析当前 section，调
 * WorkspaceTabsService.openTab()，再由 tab 条 + TabPaneComponent 自行渲染。
 *
 * 但 Angular router 要求 child route 必须 loadComponent 才能匹配 URL，
 * 否则 /project/:id/<section> 会落到 ** 通配 fallback。这里挂一个零 UI
 * 的占位组件，让路由匹配走通，但实际页面内容由 shell 内部用 service 渲染。
 *
 * 使用 RouterOutlet 不会出现在 DOM 上（shell 没有 outlet），所以这里
 * 返回空 host 即可。
 */
@Component({
  selector: 'app-section-placeholder',
  standalone: true,
  template: '',
  styles: [':host { display: none; }'],
  encapsulation: ViewEncapsulation.None,
})
export class SectionPlaceholderComponent {}
