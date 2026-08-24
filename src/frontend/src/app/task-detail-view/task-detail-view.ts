import { CommonModule } from '@angular/common';
import { Component, ViewEncapsulation, inject } from '@angular/core';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import { ProjectDataService } from '../services/project-data.service';

/** Task detail rendered as an entity tab inside the project workspace. */
@Component({
  selector: 'app-task-detail-view',
  standalone: true,
  imports: [CommonModule, WorkspaceHeadingComponent],
  templateUrl: './task-detail-view.html',
  encapsulation: ViewEncapsulation.None,
})
export class TaskDetailViewComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  openEntity(event: MouseEvent, kind: 'epic' | 'story' | 'task', id: number, title?: string): void {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    void this.host.openWorkspaceEntity(kind, id, title);
  }
}
