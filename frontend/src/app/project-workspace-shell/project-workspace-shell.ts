import { Component, Input, ViewEncapsulation, computed } from '@angular/core';
import { RouterLink } from '@angular/router';
import type { Project, ProjectTabKind } from '../models';

/**
 * ProjectWorkspaceShellComponent — Epic 152 / Story 332 (Sub-PR 1)
 *
 * 项目工作台外壳：从 app.html line 240-285 抽出的 navy sidebar（8 tab routerLink）+
 * 右侧内容区。
 *
 * Sub-PR 1 阶段角色（shim）：
 * - 独立 standalone 组件，编译通过
 * - 数据通过 @Input 接收（与 app.html 原写法兼容）
 * - 模板：navy sidebar + 后续 children 区（Sub-PR 1b 阶段改 router-outlet）
 *
 * Sub-PR 1b 阶段（路由完全收口 shim）：
 * - app.html @case ('project') 块改为 <app-project-workspace-shell>
 * - 8 tab children 路由由 app.routes.ts children 接管
 * - 组件内 <router-outlet> 渲染 children
 * - app.ts loadRoute() 写 ProjectDataService；8 tab 改 inject service
 *
 * Story 333 阶段（彻底清场）：
 * - 8 tab 自管 ActivatedRoute 拿 :id
 * - app.ts 移除 8 tab @Input signal 引用
 *
 * 视觉：与原 app.html line 240-285 完全一致（ViewEncapsulation.None 全局样式）。
 */
@Component({
  selector: 'app-project-workspace-shell',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './project-workspace-shell.html',
  encapsulation: ViewEncapsulation.None,
})
export class ProjectWorkspaceShellComponent {
  @Input() project: Project | null = null;
  @Input() activeTab: ProjectTabKind = 'overview';
  @Input() sidebarOpen = true;
  @Input() onlineAgentCount = 0;

  // 8 tab 配置（命名 freeze from docs/ia-guidelines/sidebar-capacity.md）
  // 与 app.html line 253-274 的 8 tab 保持一致（routerLink + 图标 + 中文 label）
  readonly tabs: ReadonlyArray<{ kind: ProjectTabKind; path: string; label: string; iconId: string; ariaLabel: string }> = [
    { kind: 'overview',  path: 'overview',  label: '概览',          iconId: 'i-home',     ariaLabel: '概览' },
    { kind: 'kanban',    path: 'kanban',    label: '看板',          iconId: 'i-board',    ariaLabel: '看板' },
    { kind: 'epics',     path: 'epics',     label: 'Epics',         iconId: 'i-flag',     ariaLabel: 'Epics' },
    { kind: 'backlog',   path: 'backlog',   label: '工作项',        iconId: 'i-list',     ariaLabel: '工作项' },
    { kind: 'proposals', path: 'proposals', label: '提案',          iconId: 'i-message',  ariaLabel: '提案' },
    { kind: 'documents', path: 'documents', label: '文档',          iconId: 'i-file',     ariaLabel: '文档' },
    { kind: 'members',   path: 'members',   label: '成员与 Agents', iconId: 'i-users',    ariaLabel: '成员与 Agents' },
    { kind: 'settings',  path: 'settings',  label: '设置',          iconId: 'i-settings', ariaLabel: '设置' },
  ];

  // backlog 双语义：activeTab() === 'tickets' 也高亮 backlog（兼容旧 loadRoute）
  isBacklogActive = computed(() => this.activeTab === 'backlog' || this.activeTab === ('tickets' as ProjectTabKind));

  projectMonogram = computed(() => {
    const p = this.project;
    if (!p) return 'AB';
    return (p.name || p.key || 'AB').slice(0, 2).toUpperCase();
  });

  projectName = computed(() => this.project?.name || '未选择项目');
  projectMeta = computed(() => this.project?.key || '项目工作台');
}
