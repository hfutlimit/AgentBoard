import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { KanbanStory } from '../models';

/**
 * KanbanTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「看板」视图独立组件。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区看板 tab 从 4900+ 行的单体模板中拆出，套用原型 v7 的卡片视觉骨架
 * （workspace-card 风格的列头 + kanban-card），同时保留原有业务逻辑（状态分桶、
 * Story 卡片含 task 概要、进入/移出看板标记、按 status 过滤）。
 *
 * 数据契约（@Input）：
 *   columns       已分桶+过滤后的看板列（来自 App.kanbanColumns()）
 *   count         看板卡片总数（来自 App.kanbanCount()）
 *   includeAll    是否包含未标记进入看板的 Story（来自 App.kanbanIncludeAll()）
 *   loading       看板 tab 是否加载中（来自 App.isProjectTabLoading('kanban')）
 *   error         看板 tab 加载错误信息（来自 App.projectTabError('kanban')）
 *   skeletonRows  骨架屏占位数组（来自 App.tabSkeletonRows）
 *
 * 事件契约（@Output）：
 *   toggleIncludeAll  切换「显示全部 / 只看看板标记」（替代 App.toggleKanbanIncludeAll）
 *   retry             重试加载（替代 App.retryProjectTab('kanban')）
 *   openStory         点击卡片打开 Story（替代 App.openKanbanStory）
 *   toggleStory       卡片内进入/移出看板按钮（替代 App.toggleKanbanStory）
 *
 * 视觉：列/卡片直接复用全局 .kanban-col / .kanban-card 规则（ViewEncapsulation.None），
 * 与 App 组件封装策略一致；过渡期 app.css 旧规则共存，阶段4 色板收口时统一清理。
 */
@Component({
  selector: 'app-kanban-tab',
  standalone: true,
  imports: [WorkspaceHeadingComponent],
  templateUrl: './kanban-tab.html',
  styleUrl: './kanban-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class KanbanTabComponent {
  /** 已分桶+过滤后的看板列（status → stories）。 */
  @Input({ required: true }) columns: Array<{ status: string; stories: KanbanStory[] }> = [];
  /** 看板卡片总数。 */
  @Input() count = 0;
  /** 是否包含未标记进入看板的 Story。 */
  @Input() includeAll = false;
  /** 看板 tab 是否加载中。 */
  @Input() loading = false;
  /** 看板 tab 加载错误信息（空串表示无错误）。 */
  @Input() error = '';
  /** 骨架屏占位数组。 */
  @Input() skeletonRows: number[] = [];

  @Output() toggleIncludeAll = new EventEmitter<void>();
  @Output() retry = new EventEmitter<void>();
  @Output() openStory = new EventEmitter<KanbanStory>();
  @Output() toggleStory = new EventEmitter<KanbanStory>();

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

  /** 状态色点（与 App.statusColor 一致）。 */
  statusColor(status: string): string {
    return (
      { todo: '#0EA5E9', in_progress: '#5B5BD6', in_review: '#7C3AED', done: '#16A34A', blocked: '#DC2626' } as Record<
        string,
        string
      >
    )[status] || '#94a3b8';
  }

  /** 任务类型文案（与 App.kanbanTaskTypeLabel 一致）。 */
  kanbanTaskTypeLabel(type: string): string {
    return type === 'design' ? '设计' : type === 'bug' ? 'Bug' : type === 'qa' ? 'QA' : '开发';
  }

  /** 任务类型样式类（与 App.kanbanTaskClass 一致）。 */
  kanbanTaskClass(type: string): string {
    return type === 'design' ? 'kb-t-design' : type === 'bug' ? 'kb-t-bug' : type === 'qa' ? 'kb-t-qa' : 'kb-t-dev';
  }
}
