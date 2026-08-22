import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { Task } from '../models';

/**
 * BacklogTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「Backlog」视图独立组件（6/8）。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区 backlog tab 从单体模板中拆出，套用原型 v7 的卡片视觉骨架
 * （entity-item 加 brand 描边 + type-icon navy 圆底 + badge color-mix ring），
 * 同时保留原有业务逻辑（任务列表分页 / 类型图标 / 状态+优先级 badge / 空状态）。
 *
 * 与 ManagedListComponent 关系：
 *   backlog tab 在阶段2 已套用 ManagedListComponent 外壳，本次将「外壳 + 主体」整体抽出。
 *
 * 数据契约（@Input）：
 *   tasks     已过滤后的 backlog 任务列表（来自 App.backlogVisibleTasks()）
 *   page      当前分页（来自 App.backlogPage()）
 *   pageSize  分页大小（来自 App.projectListPageSize）
 *   loading   backlog tab 是否加载中
 *   error     backlog tab 加载错误信息
 *
 * 事件契约（@Output）：
 *   pageChange  分页变更（替代 App.setProjectListPage('backlog', $event)）
 *   retry       重试加载（替代 App.retryProjectTab('backlog')）
 *
 * 视觉：基础规则复用全局 .entity-item / .entity-list / .type-icon
 * （ViewEncapsulation.None），本组件 css 仅补 v7 增强。
 */
@Component({
  selector: 'app-backlog-tab',
  standalone: true,
  imports: [ManagedListComponent, WorkspaceHeadingComponent],
  templateUrl: './backlog-tab.html',
  styleUrl: './backlog-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class BacklogTabComponent {
  @Input({ required: true }) tasks: Task[] = [];
  @Input() page = 1;
  @Input() pageSize = 20;
  @Input() loading = false;
  @Input() error = '';

  @Output() pageChange = new EventEmitter<number>();
  @Output() retry = new EventEmitter<void>();
  @Output() openTask = new EventEmitter<{ event: MouseEvent; task: Task }>();

  /** 类型图标字符（与 App.typeGlyph 一致，纯函数复制）。 */
  typeGlyph(type: string): string {
    if (type === 'bug') return 'B';
    if (type === 'qa') return 'Q';
    if (type === 'design') return 'D';
    return 'T';
  }

  /** 状态文案（与 App.statusLabel 一致，纯函数复制）。 */
  statusLabel(status: string): string {
    return (
      (
        {
          todo: '待办',
          in_progress: '进行中',
          in_review: '评审中',
          done: '完成',
          blocked: '已阻塞',
        } as Record<string, string>
      )[status] || status
    );
  }

  /** 优先级文案（与 App.priorityLabel 一致，纯函数复制）。 */
  priorityLabel(priority: string): string {
    return (
      (
        { highest: '最高', high: '高', medium: '中', low: '低', lowest: '最低' } as Record<string, string>
      )[priority] || priority
    );
  }

  /** 分页切片（与 App.paginatedItems 一致，纯函数复制）。 */
  paginatedItems<T>(items: T[], page: number): T[] {
    const totalPages = Math.max(1, Math.ceil(items.length / this.pageSize));
    const currentPage = Math.min(Math.max(1, page), totalPages);
    const start = (currentPage - 1) * this.pageSize;
    return items.slice(start, start + this.pageSize);
  }
}
