import { CommonModule, DOCUMENT } from '@angular/common';
import {
  Component,
  HostListener,
  ViewEncapsulation,
  computed,
  inject,
} from '@angular/core';
import { ProjectDataService } from '../../services/project-data.service';
import { WorkspaceDrawerService } from '../../services/workspace-drawer.service';
import { TaskDetailViewComponent } from '../../task-detail-view/task-detail-view';
import { StoryDetailViewComponent } from '../../story-detail-view/story-detail-view';

/**
 * WorkspaceDrawerComponent — 工作台右侧 Drawer（Story #435）
 *
 * 承载「瞄一眼就走」的实体详情（默认 Task）。与 Tab 的区别：
 * - Drawer 不占 Tab 条，关掉不留痕
 * - 需要长期驻留时点头部「在 Tab 中打开」，才提升为常驻 Tab
 *
 * 数据加载复用 host.openWorkspaceEntity(drawer) 的既有链路，
 * 不新增第二条加载路径，避免详情内容在 Drawer / Tab 两处表现不一致。
 */
@Component({
  selector: 'app-workspace-drawer',
  standalone: true,
  imports: [CommonModule, TaskDetailViewComponent, StoryDetailViewComponent],
  templateUrl: './workspace-drawer.html',
  styleUrl: './workspace-drawer.css',
  encapsulation: ViewEncapsulation.None,
})
export class WorkspaceDrawerComponent {
  private readonly document = inject(DOCUMENT);
  readonly drawer = inject(WorkspaceDrawerService);
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  readonly typeLabel = computed(() => {
    const state = this.drawer.state();
    if (!state) return '';
    return state.kind === 'task' ? 'Task' : state.kind === 'story' ? 'Story' : state.kind === 'epic' ? 'Epic' : '提案';
  });

  readonly heading = computed(() => {
    const state = this.drawer.state();
    if (!state) return '';
    if (state.title) return state.title;
    const detail = state.kind === 'task' ? this.host.task() : state.kind === 'story' ? this.host.story() : null;
    const t = detail as { title?: string } | null;
    return t?.title || `${this.typeLabel()} #${state.entityId}`;
  });

  /** 父级路径：Story / Task 显示所属 Epic › Story，保证层级一直可见 */
  readonly parentPath = computed(() => {
    const state = this.drawer.state();
    if (!state) return '';
    const epic = this.host.epic() as { id?: number; title?: string } | null;
    const story = this.host.story() as { id?: number; title?: string } | null;
    const parts: string[] = [];
    if (state.kind === 'task' || state.kind === 'story') {
      if (epic?.title) parts.push(epic.title);
      if (state.kind === 'task' && story?.title) parts.push(story.title);
    }
    return parts.join(' › ');
  });

  /** 目标 id 尚未加载完成时显示骨架，避免闪一下空面板 */
  readonly pending = computed(() => {
    const state = this.drawer.state();
    if (!state) return false;
    if (state.kind === 'task') {
      const task = this.host.task() as { id?: number } | null;
      return !task || task.id !== state.entityId;
    }
    if (state.kind === 'story') {
      const story = this.host.story() as { id?: number } | null;
      return !story || story.id !== state.entityId;
    }
    return false;
  });

  private dragStartX = 0;
  private dragStartWidth = 0;
  private dragging = false;

  close(): void {
    this.drawer.close();
  }

  /** 提升为常驻 Tab：走与实体链接一致的 host 入口，保证 URL / 数据一致 */
  promoteToTab(): void {
    const state = this.drawer.state();
    if (!state) return;
    // Drawer 里存的是 `Task · xxx` 形式的标签，回传 host 时要还原成原始标题，
    // 否则 host 会再包一层前缀变成 `Task · Task · xxx`。
    const rawTitle = state.title?.replace(/^(Epic|提案|Story|Task)\s·\s/, '');
    void this.host.openWorkspaceEntity(state.kind, state.entityId, rawTitle, { asTab: true });
    this.drawer.close();
  }

  onBackdropClick(): void {
    this.close();
  }

  startResize(event: MouseEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragging = true;
    this.dragStartX = event.clientX;
    this.dragStartWidth = this.drawer.width();
    this.document.body.classList.add('ws-drawer-resizing');
  }

  @HostListener('document:mousemove', ['$event'])
  onMouseMove(event: MouseEvent): void {
    if (!this.dragging) return;
    // Drawer 贴右边缘：向左拖变宽
    const delta = this.dragStartX - event.clientX;
    this.drawer.setWidth(this.dragStartWidth + delta);
  }

  @HostListener('document:mouseup')
  onMouseUp(): void {
    if (!this.dragging) return;
    this.dragging = false;
    this.document.body.classList.remove('ws-drawer-resizing');
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.drawer.isOpen()) this.close();
  }
}
