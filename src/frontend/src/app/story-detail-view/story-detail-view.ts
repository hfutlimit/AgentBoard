import { CommonModule } from '@angular/common';
import { Component, ViewEncapsulation, inject } from '@angular/core';
import { ProjectDataService } from '../services/project-data.service';
import { WorkerDiscussionComponent } from '../worker-discussion/worker-discussion';

/** Story detail rendered as an entity tab inside the project workspace. */
@Component({
  selector: 'app-story-detail-view',
  standalone: true,
  imports: [CommonModule, WorkerDiscussionComponent],
  templateUrl: './story-detail-view.html',
  encapsulation: ViewEncapsulation.None,
})
export class StoryDetailViewComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  openEntity(event: MouseEvent, kind: 'epic' | 'task', id: number, title?: string): void {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    void this.host.openWorkspaceEntity(kind, id, title);
  }
}
