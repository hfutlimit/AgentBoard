import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { ProposalItem, ProposalStatus } from '../models';

/**
 * ProposalsTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「提案」视图独立组件（4/8）。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区 proposals tab 从单体模板中拆出，套用原型 v7 的卡片视觉骨架
 * （proposal-row 加 brand 描边 + status badge color-mix ring + 工具栏 navy 下划线），
 * 同时保留原有业务逻辑（状态过滤 / 搜索 / 提案列表 / 新建入口 / 路由跳转）。
 *
 * 与 ManagedListComponent 关系：
 *   proposals tab 在阶段2 已套用 ManagedListComponent 外壳，本次将「外壳 + 主体」整体抽出。
 *
 * 数据契约（@Input）：
 *   proposals    已过滤+搜索后的提案列表（来自 App.proposalVisible()）
 *   filterStatus 当前状态过滤值（来自 App.proposalFilterStatus()）
 *   searchQuery  当前搜索关键词（来自 App.proposalSearchQuery()）
 *   statuses     状态枚举数组（来自 App.proposalStatuses = PROPOSAL_STATUSES）
 *   loading      proposals tab 是否加载中
 *   error        proposals tab 加载错误信息
 *
 * 事件契约（@Output）：
 *   filterStatusChange  状态过滤变更（父组件 proposalFilterStatus.set($event)）
 *   searchQueryChange   搜索关键词变更（父组件 proposalSearchQuery.set($event)）
 *   createProposal      新建提案（替代 App.openProposalModal()）
 *   retry               重试加载（替代 App.retryProjectTab('proposals')）
 *
 * 视觉：基础规则复用全局 .proposal-row / .proposal-list / .doc-toolbar
 * （ViewEncapsulation.None），本组件 css 仅补 v7 增强。
 */
@Component({
  selector: 'app-proposals-tab',
  standalone: true,
  imports: [ManagedListComponent, RouterLink, WorkspaceHeadingComponent],
  templateUrl: './proposals-tab.html',
  styleUrl: './proposals-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class ProposalsTabComponent {
  @Input({ required: true }) proposals: ProposalItem[] = [];
  @Input() filterStatus: ProposalStatus | '' = '';
  @Input() searchQuery = '';
  @Input() statuses: ProposalStatus[] = [];
  @Input() loading = false;
  @Input() error = '';

  @Output() filterStatusChange = new EventEmitter<ProposalStatus | ''>();
  @Output() searchQueryChange = new EventEmitter<string>();
  @Output() createProposal = new EventEmitter<void>();
  @Output() retry = new EventEmitter<void>();

  /** 提案状态文案（与 App.proposalStatusLabel 一致，纯函数复制）。 */
  proposalStatusLabel(s: ProposalStatus): string {
    return ({
      draft: '草稿',
      pending: '待开始',
      queued: '已入队',
      analyzing: '分析中',
      awaiting: '待作答',
      answered: '已作答',
      converged: '需求已明确',
      story_created: '已转 Story',
      ticket_preparing: '工单生成中',
      ticket_created: '已生成工单',
      failed: '失败',
    } as Record<string, string>)[s] || s;
  }

  /** 相对时间（与 App.timeAgo 一致，纯函数复制）。 */
  timeAgo(dateStr: string): string {
    if (!dateStr) return '';
    const now = Date.now();
    const date = new Date(dateStr).getTime();
    const diff = Math.floor((now - date) / 1000);
    if (diff < 60) return `${diff}s前`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h前`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d前`;
    return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  }
}
