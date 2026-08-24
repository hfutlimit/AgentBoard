import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import type { TicketItem } from '../models';

type TicketFilter = 'all' | 'incomplete' | 'complete';
type TicketSort = 'created_at' | 'updated_at';
type TicketOrder = 'asc' | 'desc';

/**
 * TicketsTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「工单」视图独立组件（7/8）。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区 tickets tab 从单体模板中拆出，套用原型 v7 的卡片视觉骨架
 * （ticket-row 加 brand 描边 + seg 控件 navy 高亮 + badge color-mix ring），
 * 同时保留原有业务逻辑（完成状态过滤 / 排序 / 工单列表 / 点击打开详情）。
 *
 * 与 ManagedListComponent 关系：
 *   tickets tab 不使用 ManagedListComponent（有自己的 tab-content + 三态壳 + seg 过滤控件），
 *   保持独立三态壳不变，仅抽出为独立组件 + 套 v7 视觉。
 *
 * 数据契约（@Input）：
 *   tickets     工单列表（来自 App.tickets()）
 *   filter      完成状态过滤（来自 App.ticketFilter()）
 *   sort        排序字段（来自 App.ticketSort()）
 *   order       排序方向（来自 App.ticketOrder()）
 *   loading     tickets tab 是否加载中
 *   error       tickets tab 加载错误信息
 *   skeletonRows 骨架屏占位数组（来自 App.tabSkeletonRows）
 *
 * 事件契约（@Output）：
 *   filterChange   过滤变更（替代 App.setTicketFilter($event)）
 *   sortChange     排序字段变更（替代 App.setTicketSort($event)）
 *   orderChange    排序方向变更（替代 App.setTicketOrder($event)）
 *   openTicket     点击工单（替代 App.openTicket($event)）
 *   retry          重试加载（替代 App.retryProjectTab('tickets')）
 *
 * 视觉：基础规则复用全局 .ticket-list / .ticket-row / .seg / .tab-list-skeleton
 * （ViewEncapsulation.None），本组件 css 仅补 v7 增强。
 */
@Component({
  selector: 'app-tickets-tab',
  standalone: true,
  templateUrl: './tickets-tab.html',
  styleUrl: './tickets-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class TicketsTabComponent {
  @Input({ required: true }) tickets: TicketItem[] = [];
  @Input() filter: TicketFilter = 'incomplete';
  @Input() sort: TicketSort = 'created_at';
  @Input() order: TicketOrder = 'desc';
  @Input() loading = false;
  @Input() error = '';
  @Input() skeletonRows: number[] = [];

  @Output() filterChange = new EventEmitter<TicketFilter>();
  @Output() sortChange = new EventEmitter<TicketSort>();
  @Output() orderChange = new EventEmitter<TicketOrder>();
  @Output() openTicket = new EventEmitter<TicketItem>();
  @Output() retry = new EventEmitter<void>();

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
}
