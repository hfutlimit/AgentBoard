import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import type { ProjectStats, ReviewStats, ReviewTimeoutResult } from '../models';

/**
 * StatsTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「统计」视图独立组件（8/8，最后一个）。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区 stats tab 从单体模板中拆出，套用原型 v7 的 metric-card 视觉骨架
 * （metric 卡 navy 大数字 / chart-bar brand 渐变 / review-ops panel navy 渐变头 /
 * stat-card highlight brand 强调色），同时保留原有业务逻辑（项目统计 / 每日新增-完成柱状图 /
 * 评审运营面板：重派超时 / 多数决投票进度 / 评审人工作量条形图）。
 *
 * 与 ManagedListComponent 关系：
 *   stats tab 不使用 ManagedListComponent（无搜索/筛选/分页需求，是只读统计 + 操作面板），
 *   保持独立 tab-content + 三态壳（loading/error/主体），仅抽出为独立组件 + 套 v7 视觉。
 *
 * 数据契约（@Input）：
 *   loading              stats tab 是否加载中（来自 App.isProjectTabLoading('stats')）
 *   error                stats tab 加载错误（来自 App.projectTabError('stats')）
 *   stats                项目统计（来自 App.projectStats()）
 *   statsMaxCreated      每日新增任务最大值（来自 App.statsMaxCreated()，柱状图比例）
 *   statsMaxDone         每日完成任务最大值（来自 App.statsMaxDone()，柱状图比例）
 *   reviewStats          评审统计（来自 App.reviewStats()）
 *   reviewStatsLoading   评审统计加载中（来自 App.reviewStatsLoading()）
 *   reviewStatsError     评审统计加载错误（来自 App.reviewStatsError()）
 *   reviewReassignBusy   重派超时扫描中（来自 App.reviewReassignBusy()）
 *   reviewReassignResult 重派结果（来自 App.reviewReassignResult()）
 *   projectId            当前项目 id（用于重试加载评审统计 App.loadReviewStats(current.id)）
 *   skeletonRows         骨架屏占位数组（来自 App.tabSkeletonRows）
 *
 * 事件契约（@Output）：
 *   retryProjectStats    重试加载项目统计（替代 App.retryProjectTab('stats')）
 *   retryReviewStats     重试加载评审统计（替代 App.loadReviewStats(current.id)）
 *   triggerReassign      触发超时重派（替代 App.triggerReassignTimeout()）
 *
 * 纯函数（与 App 同名方法一致，子组件内复制）：
 *   reviewModeLabel / reviewVotePct / reviewVoteReached / maxReviewerReviewed / reviewerReviewed
 *
 * 视觉：基础规则复用全局 .tab-content / .stats-grid / .stat-card / .stats-chart /
 * .chart-bars / .review-ops-panel / .review-vote-* / .review-reviewer-*
 * （ViewEncapsulation.None），本组件 css 仅补 v7 增强。
 */
@Component({
  selector: 'app-stats-tab',
  standalone: true,
  templateUrl: './stats-tab.html',
  styleUrl: './stats-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class StatsTabComponent {
  @Input() loading = false;
  @Input() error = '';
  @Input() stats: ProjectStats | null = null;
  @Input() statsMaxCreated = 1;
  @Input() statsMaxDone = 1;
  @Input() reviewStats: ReviewStats | null = null;
  @Input() reviewStatsLoading = false;
  @Input() reviewStatsError = '';
  @Input() reviewReassignBusy = false;
  @Input() reviewReassignResult: ReviewTimeoutResult | null = null;
  @Input() projectId: number | null = null;
  @Input() skeletonRows: number[] = [];

  @Output() retryProjectStats = new EventEmitter<void>();
  @Output() retryReviewStats = new EventEmitter<number>();
  @Output() triggerReassign = new EventEmitter<void>();

  /** 评审模式可读标签（single=单人评审 / majority=多数决评审）。 */
  reviewModeLabel(mode?: string): string {
    return mode === 'majority' ? '多数决评审' : '单人评审';
  }

  /** 投票进度条百分比（0..100，quorum 恒 >0 由后端保证）。 */
  reviewVotePct(row: { cast: number; quorum: number }): number {
    const cast = Number(row?.cast ?? 0);
    const quorum = Number(row?.quorum ?? 0);
    if (!(quorum > 0)) return 0;
    return Math.min(100, Math.round((cast / quorum) * 100));
  }

  /** 投票是否已达法定票数（可结算）。 */
  reviewVoteReached(row: { cast: number; quorum: number }): boolean {
    const cast = Number(row?.cast ?? 0);
    const quorum = Number(row?.quorum ?? 0);
    return quorum > 0 && cast >= quorum;
  }

  /** 评审人工作量条形图最大值（S4 运营视图）。 */
  maxReviewerReviewed(rs: ReviewStats): number {
    return rs.by_reviewer.reduce((m, r) => Math.max(m, r.story_reviewed + r.task_reviewed), 0);
  }

  /** 评审人评审总数（Story + Task，S4 运营视图）。 */
  reviewerReviewed(r: { story_reviewed: number; task_reviewed: number }): number {
    return r.story_reviewed + r.task_reviewed;
  }
}
