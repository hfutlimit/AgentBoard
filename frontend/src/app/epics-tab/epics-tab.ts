import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { Epic } from '../models';

/**
 * EpicProgress — Epic 进度统计（与 App.epicProgress 返回结构对齐）。
 * 由父组件通过 epicProgressFor 函数 Input 提供，避免子组件重复持有 stories/tasks
 * 全量数据，保持单一数据源。
 */
export interface EpicProgress {
  stories: number;
  doneStories: number;
  tasks: number;
  doneTasks: number;
  pct: number;
}

/**
 * EpicsTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「Epics」视图独立组件（3/8）。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区 epics tab 从 4900+ 行的单体模板中拆出，套用原型 v7 的卡片视觉骨架
 * （entity-item 加 navy 渐变进度条 + 状态点 color-mix ring + Epic 图标 navy 圆底），
 * 同时保留原有业务逻辑（Epic 列表分页 / 进度统计 / 空项目引导 / 状态 badge / 新建入口）。
 *
 * 与 ManagedListComponent 关系：
 *   epics tab 在阶段2 已套用 ManagedListComponent 外壳（loading/error/分页/空状态三态壳），
 *   本次将「外壳 + 主体」整体抽出为独立组件，外壳由本组件模板内嵌 ManagedListComponent 提供。
 *
 * 数据契约（@Input）：
 *   epics            已过滤+排序后的 Epic 列表（来自 App.visibleEpics()）
 *   epicProgressFor  Epic 进度查询函数（来自 App.epicProgress）—— 函数 Input 保持单一数据源
 *   page             当前分页（来自 App.epicsPage()）
 *   pageSize         分页大小（来自 App.projectListPageSize）
 *   loading          epics tab 是否加载中（来自 App.isProjectTabLoading('epics')）
 *   error            epics tab 加载错误信息（来自 App.projectTabError('epics')）
 *   projectId        当前项目 ID（用于新建 Epic 入口 emit）
 *
 * 事件契约（@Output）：
 *   pageChange   分页变更（替代 App.setProjectListPage('epics', $event)）
 *   retry        重试加载（替代 App.retryProjectTab('epics')）
 *   createEpic   新建 Epic（替代 App.openCreate('epic', current.id)）
 *
 * 视觉：基础规则复用全局 .entity-item / .epic-progress-mini / .type-icon.epic
 * （ViewEncapsulation.None），本组件 css 仅补 v7 增强（navy 渐变 / color-mix ring / 暗色提亮）。
 */
@Component({
  selector: 'app-epics-tab',
  standalone: true,
  imports: [ManagedListComponent, RouterLink, WorkspaceHeadingComponent],
  templateUrl: './epics-tab.html',
  styleUrl: './epics-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class EpicsTabComponent {
  /** 已过滤+排序后的 Epic 列表。 */
  @Input({ required: true }) epics: Epic[] = [];
  /** Epic 进度查询函数（来自 App.epicProgress）。 */
  @Input() epicProgressFor: (id: number) => EpicProgress = () => ({
    stories: 0,
    doneStories: 0,
    tasks: 0,
    doneTasks: 0,
    pct: 0,
  });
  /** 当前分页（1-based）。 */
  @Input() page = 1;
  /** 分页大小。 */
  @Input() pageSize = 20;
  /** epics tab 是否加载中。 */
  @Input() loading = false;
  /** epics tab 加载错误信息（空串表示无错误）。 */
  @Input() error = '';
  /** 当前项目 ID（用于新建 Epic emit）。 */
  @Input() projectId: number | null = null;

  @Output() pageChange = new EventEmitter<number>();
  @Output() retry = new EventEmitter<void>();
  @Output() createEpic = new EventEmitter<number>();

  /** 状态文案（与 App.statusLabel 一致，纯函数复制以避免跨组件依赖）。 */
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

  /** 分页切片（与 App.paginatedItems 一致）。 */
  paginatedItems<T>(items: T[], page: number): T[] {
    const totalPages = Math.max(1, Math.ceil(items.length / this.pageSize));
    const currentPage = Math.min(Math.max(1, page), totalPages);
    const start = (currentPage - 1) * this.pageSize;
    return items.slice(start, start + this.pageSize);
  }
}
