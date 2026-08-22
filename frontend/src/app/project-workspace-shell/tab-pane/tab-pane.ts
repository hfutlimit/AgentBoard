import { Component, EventEmitter, Input, Output, ViewEncapsulation, inject } from '@angular/core';
import { BacklogTabComponent } from '../../backlog-tab/backlog-tab';
import { DocumentsTabComponent } from '../../documents-tab/documents-tab';
import { EpicsTabComponent } from '../../epics-tab/epics-tab';
import { KanbanTabComponent } from '../../kanban-tab/kanban-tab';
import { MembersTabComponent } from '../../members-tab/members-tab';
import { OverviewTabComponent } from '../../overview-tab/overview-tab';
import { ProposalsTabComponent } from '../../proposals-tab/proposals-tab';
import { ProjectDataService } from '../../services/project-data.service';
import { SettingsTabComponent } from '../../settings-tab/settings-tab';
import { EpicDetailViewComponent } from '../../epic-detail-view/epic-detail-view';
import { ProposalDetailViewComponent } from '../../proposal-detail-view/proposal-detail-view';
import { StoryDetailViewComponent } from '../../story-detail-view/story-detail-view';
import { TaskDetailViewComponent } from '../../task-detail-view/task-detail-view';
import type { WorkspaceTab } from '../../services/workspace-tabs.service';

export type DetailKind = 'story' | 'task' | 'epic' | 'proposal' | 'sprint' | 'document';
export interface DetailSelection { kind: DetailKind; id: number; }

/**
 * TabPaneComponent — 单个 tab 的内容容器
 *
 * 2026-08-21 结构调整：把 ProjectWorkspaceRouteComponent 的 dispatcher 逻辑
 * 抽到这里。每开一个 tab 就挂一个 TabPaneComponent 实例，Angular 自动保留
 * 内部状态（筛选、滚动、已加载数据）。非激活的 pane 由外层用 [class.hidden]
 * 触发 CSS display:none，组件不销毁。
 *
 * 2026-08-21 v3 修：*-tab 内部点详情 link 由 ProjectWorkspaceShellComponent
 * 的 document-level capture-phase click 拦截器统一处理（见 shell.ts）,
 * shell 接收后 emit openDetail → 调 side panel。tab-pane 这里不重复拦截,
 * 只做 event 转发（如果 *-tab 用 emit 形式而非 routerLink,这里转发）。
 */
@Component({
  selector: 'app-tab-pane',
  standalone: true,
  imports: [
    OverviewTabComponent,
    KanbanTabComponent,
    EpicsTabComponent,
    BacklogTabComponent,
    ProposalsTabComponent,
    DocumentsTabComponent,
    MembersTabComponent,
    SettingsTabComponent,
    EpicDetailViewComponent,
    ProposalDetailViewComponent,
    StoryDetailViewComponent,
    TaskDetailViewComponent,
  ],
  templateUrl: './tab-pane.html',
  styleUrl: './tab-pane.css',
  encapsulation: ViewEncapsulation.None,
})
export class TabPaneComponent {
  @Input({ required: true }) tab!: WorkspaceTab;

  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  openEntity(
    event: MouseEvent,
    kind: 'epic' | 'proposal' | 'story' | 'task',
    entityId: number,
    title: string,
  ): void {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    this.host.openWorkspaceEntity(kind, entityId, title);
  }
}
