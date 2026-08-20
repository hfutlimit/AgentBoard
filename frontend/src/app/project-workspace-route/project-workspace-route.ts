import { Component, ViewEncapsulation, inject } from '@angular/core';
import { Router } from '@angular/router';
import { BacklogTabComponent } from '../backlog-tab/backlog-tab';
import { DocumentsTabComponent } from '../documents-tab/documents-tab';
import { EpicsTabComponent } from '../epics-tab/epics-tab';
import { KanbanTabComponent } from '../kanban-tab/kanban-tab';
import { MembersTabComponent } from '../members-tab/members-tab';
import { OverviewTabComponent } from '../overview-tab/overview-tab';
import { ProposalsTabComponent } from '../proposals-tab/proposals-tab';
import { ProjectDataService } from '../services/project-data.service';
import { SettingsTabComponent } from '../settings-tab/settings-tab';

/**
 * Lazy route boundary for the seven extracted project tabs.
 *
 * The root App remains the temporary data/action host, but navigation state is
 * read exclusively from Router. This removes the activeTab mirror and moves
 * all tab component code out of the initial application bundle.
 */
@Component({
  selector: 'app-project-workspace-route',
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
  templateUrl: './project-workspace-route.html',
  styleUrl: './project-workspace-route.css',
  encapsulation: ViewEncapsulation.None,
})
export class ProjectWorkspaceRouteComponent {
  private readonly router = inject(Router);
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  tab(): string {
    const path = this.router.url.split('?')[0].split('/').filter(Boolean);
    return path[2] || 'overview';
  }
}
