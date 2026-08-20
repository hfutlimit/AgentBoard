import { Component, ViewEncapsulation, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import type { ProjectTabKind } from '../models';
import { ProjectDataService } from '../services/project-data.service';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';

/** Parent route for the project workspace. Navigation state belongs to Router. */
@Component({
  selector: 'app-project-workspace-shell',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet, WorkspaceHeadingComponent],
  templateUrl: './project-workspace-shell.html',
  styleUrl: './project-workspace-shell.css',
  encapsulation: ViewEncapsulation.None,
})
export class ProjectWorkspaceShellComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  readonly tabs: ReadonlyArray<{ kind: ProjectTabKind; path: string; label: string; iconId: string; ariaLabel: string }> = [
    { kind: 'overview', path: 'overview', label: '概览', iconId: 'i-home', ariaLabel: '概览' },
    { kind: 'kanban', path: 'kanban', label: '看板', iconId: 'i-board', ariaLabel: '看板' },
    { kind: 'epics', path: 'epics', label: 'Epics', iconId: 'i-flag', ariaLabel: 'Epics' },
    { kind: 'backlog', path: 'backlog', label: '工作项', iconId: 'i-list', ariaLabel: '工作项' },
    { kind: 'proposals', path: 'proposals', label: '提案', iconId: 'i-message', ariaLabel: '提案' },
    { kind: 'documents', path: 'documents', label: '文档', iconId: 'i-file', ariaLabel: '文档' },
    { kind: 'members', path: 'members', label: '成员与 Agents', iconId: 'i-users', ariaLabel: '成员与 Agents' },
    { kind: 'settings', path: 'settings', label: '设置', iconId: 'i-settings', ariaLabel: '设置' },
  ];

  readonly projectMonogram = computed(() => {
    const p = this.host.project();
    return (p?.name || p?.key || 'AB').slice(0, 2).toUpperCase();
  });
  readonly projectName = computed(() => this.host.project()?.name || '未选择项目');
  readonly projectMeta = computed(() => this.host.project()?.key || '项目工作台');
  readonly onlineAgentCount = computed(() => this.host.agents().filter((agent: any) => agent.online).length);
}
