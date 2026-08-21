import { Component, EventEmitter, inject, Input, OnChanges, OnInit, Output, signal, SimpleChanges, ViewEncapsulation } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import { ApiService } from '../api.service';
import type { ProposalItem, ProposalStatus, Project } from '../models';

/**
 * ProposalsTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 提案视图独立组件（4/8），支持 project / global 两种 scope。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 套用原型 v7 的卡片视觉骨架，同时保留原有业务逻辑（状态过滤 / 搜索 / 列表 / 新建入口 / 路由跳转）。
 *
 * scope 模式：
 *   - 'project' (默认)：从父组件 @Input 接收 proposals（已过滤+搜索），显示"需求提案"+ 创建按钮
 *   - 'global'    ：#1428 修复。独立调 api.listProposals({}) 拉所有项目提案，
 *                   显示"全局提案中心"，隐藏创建按钮（创建必须在项目内）
 *
 * 数据契约（@Input）：
 *   proposals    已过滤+搜索后的提案列表（project scope）
 *   filterStatus 当前状态过滤值
 *   searchQuery  当前搜索关键词
 *   statuses     状态枚举数组
 *   loading      proposals tab 是否加载中
 *   error        proposals tab 加载错误信息
 *   projects     全局模式下需要项目列表（用于显示提案归属项目名）
 *
 * 事件契约（@Output）：
 *   filterStatusChange / searchQueryChange / createProposal / retry
 */
@Component({
  selector: 'app-proposals-tab',
  standalone: true,
  imports: [ManagedListComponent, RouterLink, WorkspaceHeadingComponent],
  templateUrl: './proposals-tab.html',
  styleUrl: './proposals-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class ProposalsTabComponent implements OnInit, OnChanges {
  @Input() scope: 'project' | 'global' = 'project';
  /** 全局视图需要项目列表，用于提案列表显示"归属项目"列 */
  @Input() projects: Project[] = [];
  private readonly route = inject(ActivatedRoute);
  ngOnInit(): void {
    // 优先用路由 data.scope 覆盖（#1428 修复：/proposals 全局路由传 'global'）
    const routeScope = this.route.snapshot.data['scope'] as 'project' | 'global' | undefined;
    if (routeScope) this.scope = routeScope;
  }

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

  // ─── 全局模式独立数据流 ───
  private readonly api = inject(ApiService);
  readonly globalProposals = signal<ProposalItem[]>([]);
  readonly globalLoading = signal(false);
  readonly globalError = signal('');

  get effectiveProposals(): ProposalItem[] {
    return this.scope === 'global' ? this.globalProposals() : this.proposals;
  }
  get effectiveLoading(): boolean {
    return this.scope === 'global' ? this.globalLoading() : this.loading;
  }
  get effectiveError(): string {
    return this.scope === 'global' ? this.globalError() : this.error;
  }
  projectNameFor(p: ProposalItem): string {
    const proj = this.projects.find((x) => x.id === p.project_id);
    return proj ? proj.name : `#${p.project_id}`;
  }

  async ngOnChanges(changes: SimpleChanges): Promise<void> {
    if (this.scope !== 'global') return;
    if (!changes['scope'] && !changes['projects']) return;
    this.globalLoading.set(true);
    this.globalError.set('');
    try {
      const rows = await firstValueFrom(this.api.listProposals({}));
      this.globalProposals.set(Array.isArray(rows) ? rows : []);
    } catch (e: any) {
      this.globalError.set(e?.error?.detail || e?.message || '全局提案加载失败');
      this.globalProposals.set([]);
    } finally {
      this.globalLoading.set(false);
    }
  }

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
