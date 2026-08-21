import { Component, Input, ViewEncapsulation, inject } from '@angular/core';
import { BacklogTabComponent } from '../../backlog-tab/backlog-tab';
import { DocumentsTabComponent } from '../../documents-tab/documents-tab';
import { EpicsTabComponent } from '../../epics-tab/epics-tab';
import { KanbanTabComponent } from '../../kanban-tab/kanban-tab';
import { MembersTabComponent } from '../../members-tab/members-tab';
import { OverviewTabComponent } from '../../overview-tab/overview-tab';
import { ProposalsTabComponent } from '../../proposals-tab/proposals-tab';
import { ProjectDataService } from '../../services/project-data.service';
import { SettingsTabComponent } from '../../settings-tab/settings-tab';
import type { WorkspaceTab } from '../../services/workspace-tabs.service';

/**
 * TabPaneComponent — 单个 tab 的内容容器
 *
 * 2026-08-21 结构调整：把 ProjectWorkspaceRouteComponent 的 dispatcher 逻辑
 * 抽到这里。每开一个 tab 就挂一个 TabPaneComponent 实例，Angular 自动保留
 * 内部状态（筛选、滚动、已加载数据）。非激活的 pane 由外层用 [class.hidden]
 * 触发 CSS display:none，组件不销毁。
 *
 * @Input tab 决定渲染哪个子 tab 组件。所有 *-tab 组件的 @Input/@Output
 * 契约与旧 dispatcher 完全一致，仅数据源换成 ProjectDataService workspace host。
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
  ],
  templateUrl: './tab-pane.html',
  styleUrl: './tab-pane.css',
  encapsulation: ViewEncapsulation.None,
})
export class TabPaneComponent {
  @Input({ required: true }) tab!: WorkspaceTab;

  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();
}
