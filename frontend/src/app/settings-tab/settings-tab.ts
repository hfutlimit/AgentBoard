import { CommonModule } from '@angular/common';
import { Component, ViewEncapsulation, inject } from '@angular/core';
import { PaginationComponent } from '../pagination/pagination';
import { ProjectDataService } from '../services/project-data.service';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';

/** Full project settings route; state/actions are delegated to the workspace host. */
@Component({
  selector: 'app-settings-tab',
  standalone: true,
  imports: [CommonModule, PaginationComponent, WorkspaceHeadingComponent],
  templateUrl: './settings-tab.html',
  styleUrl: './settings-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class SettingsTabComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();
  readonly members = this.host.members;
  readonly schedules = this.host.schedules;
  readonly settingsSubTab = this.host.settingsSubTab;
  readonly membersPage = this.host.membersPage;
  readonly schedulesPage = this.host.schedulesPage;
  readonly tabSkeletonRows = this.host.tabSkeletonRows;
  readonly projectListPageSize = this.host.projectListPageSize;

  readonly isProjectTabLoading = this.host.isProjectTabLoading.bind(this.host);
  readonly projectTabError = this.host.projectTabError.bind(this.host);
  readonly retryProjectTab = this.host.retryProjectTab.bind(this.host);
  readonly selectSettingsSubTab = this.host.selectSettingsSubTab.bind(this.host);
  readonly isOwner = this.host.isOwner.bind(this.host);
  readonly isAdmin = this.host.isAdmin.bind(this.host);
  readonly saveProjectSettings = this.host.saveProjectSettings.bind(this.host);
  readonly remove = this.host.remove.bind(this.host);
  readonly paginatedItems = this.host.paginatedItems.bind(this.host);
  readonly getMemberAvatar = this.host.getMemberAvatar.bind(this.host);
  readonly removeMember = this.host.removeMember.bind(this.host);
  readonly updateMemberRole = this.host.updateMemberRole.bind(this.host);
  readonly setProjectListPage = this.host.setProjectListPage.bind(this.host);
  readonly addMember = this.host.addMember.bind(this.host);
  readonly openCreateSchedule = this.host.openCreateSchedule.bind(this.host);
  readonly toggleSchedule = this.host.toggleSchedule.bind(this.host);
  readonly deleteSchedule = this.host.deleteSchedule.bind(this.host);
  readonly exportProjectTasks = this.host.exportProjectTasks.bind(this.host);
}
