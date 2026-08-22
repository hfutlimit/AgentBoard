import { Component, ViewEncapsulation, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ProjectDataService } from '../services/project-data.service';

/** Workspace Epic detail adapter that reuses the root App's existing signals and actions. */
@Component({
  selector: 'app-epic-detail-view',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './epic-detail-view.html',
  encapsulation: ViewEncapsulation.None,
})
export class EpicDetailViewComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  readonly epic = this.host.epic;
  readonly project = this.host.project;
  readonly epicTab = this.host.epicTab;
  readonly epicEditOpen = this.host.epicEditOpen;
  readonly statuses = this.host.statuses;
  readonly epicComments = this.host.epicComments;
  readonly commentAuthor = this.host.commentAuthor;
  readonly stories = this.host.stories;

  readonly statusLabel = (status: string): string => this.host.statusLabel(status);
  readonly formatDateTime = (value: string): string => this.host.formatDateTime(value);
  readonly renderMarkdown = (value: string): string => this.host.renderMarkdown(value);
  readonly saveEpic = (title: string, description: string, status: string): void =>
    this.host.saveEpic(title, description, status);
  readonly toggleCommentPreview = (): void => this.host.toggleCommentPreview();
  readonly isCommentPreviewMode = (): boolean => this.host.isCommentPreviewMode();
  readonly timeAgo = (value: string): string => this.host.timeAgo(value);
  readonly addEpicComment = (event: Event, author: string, content: string): void =>
    this.host.addEpicComment(event, author, content);
  readonly openCreate = (kind: string, parentId: number): void => this.host.openCreate(kind, parentId);
  readonly visibleStories = (): any[] => this.host.visibleStories();

  openStory(event: MouseEvent, story: any): void {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    void this.host.openWorkspaceEntity('story', story.id, story.title);
  }
}
