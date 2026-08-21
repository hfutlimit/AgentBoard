import { Component, EventEmitter, HostListener, inject, Input, Output, ViewEncapsulation, signal, computed } from '@angular/core';
import { CommonModule, DOCUMENT } from '@angular/common';
import { RouterLink } from '@angular/router';
import type { Project, AgentRow } from '../models';

/**
 * HomeShellComponent — Epic 150 / Story 321 (X1) Home Shell 项目 Master-Detail
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/agentboard-home-workspace.html §970-1080）：
 * 复刻 prototype v7 的 Home Shell：项目 Master-Detail 2-col 布局 + 「项目 / Agents」双 tab 切换。
 *
 * 数据契约（@Input）：
 *   visible     boolean — 是否实际渲染（路由层控制：view() === 'home' 时启用）
 *   projects    Project[] — 项目 Master 列表（来自 App.projectsCenter() 或 App.visibleProjects()）
 *   agents      AgentRow[] — 全局 Agent 池（来自 App.agents()）
 *   selected    Project | null — 当前 Detail 选中项目（默认第一个）
 *   me          { username: string, display_name?: string } | null — 当前用户（用于 topbar 头像）
 *
 * 事件契约（@Output）：
 *   selectProject  number  — Master 行被点击（父级更新 selectedHomeProject）
 *   newProject     void    — 「+ 新建项目」点击（父级调用 openCreate('project')）
 *   enterWorkspace number  — 「进入工作台」点击（父级路由跳转）
 *   logoutRequest  void    — 用户菜单「退出登录」（父级清理登录态）
 *
 * 状态（组件内 signal）：
 *   activeTab  'projects' | 'agents' — 当前 tab
 *
 * 视觉：
 *   - 顶部 topbar：logo + 「项目/Agents」nav + 搜索 + 通知 + 头像（prototype 一致）
 *   - Master 列表：项目 monogram + 名称 + role + 进度条 + 在线 Agent 数 + 状态点
 *   - Detail 区域：标题 + 描述 + 「进入工作台」CTA + 4 stat 卡片 + 2 panel（交付趋势/本周重点）
 *   - Agents tab：read-only Agent 池表格
 *
 * ViewEncapsulation.None：与全局 .dashboard / .stat-card 等基础类共享，避免重复定义基础规则。
 */
@Component({
  selector: 'app-home-shell',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './home-shell.html',
  styleUrl: './home-shell.css',
  encapsulation: ViewEncapsulation.None,
})
export class HomeShellComponent {
  private readonly document = inject(DOCUMENT);

  @Input({ required: true }) projects: Project[] = [];
  @Input({ required: true }) agents: AgentRow[] = [];
  @Input() selected: Project | null = null;
  @Input() me: { username: string; display_name?: string | null } | null = null;
  /** 是否实际渲染。父级通过 @if 控制，避免组件在非 home view 时无谓执行。 */
  @Input() visible = true;

  @Output() selectProject = new EventEmitter<number>();
  @Output() newProject = new EventEmitter<void>();
  @Output() enterWorkspace = new EventEmitter<number>();
  @Output() logoutRequest = new EventEmitter<void>();
 // Review 2026-08-21: 复选框"显示全部（含已暂停的）"—— emit 触发 app.ts 重新加载项目
 @Output() includeArchivedChange = new EventEmitter<boolean>();

  readonly activeTab = signal<'projects' | 'agents'>('projects');
  readonly userMenuOpen = signal(false);
  readonly projectMenuOpen = signal(false);

  /** 当前 Detail 选中项目：fallback 到第一个项目，确保 Detail 永远有数据。 */
  readonly effectiveSelected = computed<Project | null>(() => {
    if (this.selected) return this.selected;
    return this.projects[0] ?? null;
  });

  setTab(tab: 'projects' | 'agents'): void {
    this.activeTab.set(tab);
    this.closeMenus();
  }

  toggleUserMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.projectMenuOpen.set(false);
    this.userMenuOpen.update((open) => !open);
  }

  toggleProjectMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.userMenuOpen.set(false);
    this.projectMenuOpen.update((open) => !open);
  }

  closeMenus(): void {
    this.userMenuOpen.set(false);
    this.projectMenuOpen.set(false);
  }

  /** 主题切换：补 home-shell 缺失的入口（#1431 续修）。
   * app.ts 已在 topbar 提供 theme-toggle，home view 走 home-shell 自有 header
   * （与 topbar 不共享 DOM），所以在 user dropdown 加菜单项，inline 切 dataset.theme + localStorage。
   * 与 app.ts:4432 toggleTheme() 行为一致（保持单一真相应抽 ThemeService，本轮按 Epic 11
   * 单交付纪律先 inline，等下一次抽）。 */
  isDarkTheme(): boolean {
    return this.document.documentElement.dataset['theme'] === 'dark';
  }
  toggleThemeFromMenu(): void {
    const newTheme = this.isDarkTheme() ? 'light' : 'dark';
    this.document.documentElement.dataset['theme'] = newTheme;
    try { localStorage.setItem('agentboard_theme', newTheme); } catch {}
    this.closeMenus();
  }

  @HostListener('document:click')
  onDocumentClick(): void {
    this.closeMenus();
  }

  // Review 2026-08-21: 复选框"显示全部（含已暂停的）"状态
 readonly includeArchived = signal(false);

 toggleIncludeArchived(evt: Event): void {
    const checked = (evt.target as HTMLInputElement).checked;
    this.includeArchived.set(checked);
    this.includeArchivedChange.emit(checked);
  }

 /** 项目 monogram 颜色：按 key/name 哈希稳定分配 5 色（navy/green/blue/amber/steel）。 */
  monogramClass(p: Project): string {
    const seed = (p.key || p.name || '').split('').reduce((s, c) => s + c.charCodeAt(0), 0);
    return ['monogram-navy', 'monogram-green', 'monogram-blue', 'monogram-amber', 'monogram-steel'][seed % 5];
  }

  /** 项目状态点：archived 灰、private 琥珀、其它绿色。 */
  statusClass(p: Project): string {
    if (p.is_archived) return 'status-archived';
    if (p.is_private) return 'status-private';
    return 'status-active';
  }

  /** 项目进度百分比（0-100）。 */
  progress(p: Project): number {
    const total = p.task_count ?? 0;
    const done = p.task_done ?? 0;
    if (total <= 0) return 0;
    return Math.min(100, Math.round((done / total) * 100));
  }

  /** 项目内在线 Agent 数：按全局在线数 + 项目 id 模分布估算（视觉指示用）。 */
  onlineAgents(p: Project): number {
    const total = this.agents.filter((a) => a.online).length;
    if (total === 0) return 0;
    return ((p.id % 3) + 1);
  }

  /** monogram 文本：key 优先，否则 name 首字母。 */
  monogramText(p: Project): string {
    if (p.key) return p.key;
    return p.name ? p.name.charAt(0) : '?';
  }

  /** role 标签。 */
  roleLabel(p: Project): string {
    if (p.membership_role === 'owner') return 'Owner';
    return 'Member';
  }

  /** detail 副标题：key + role 衍生描述。 */
  detailSubtitle(p: Project): string {
    const key = p.key || '—';
    const role = this.roleLabel(p);
    const meta = p.membership_role === 'owner' ? 'Product Engineering' : role;
    return `${key} · ${meta}`;
  }

  /** 用户首字母（avatar）。 */
  userInitial(): string {
    const name = this.me?.display_name || this.me?.username || 'U';
    return name.charAt(0).toUpperCase();
  }

  /** 用户显示名。 */
  userName(): string {
    return this.me?.display_name || this.me?.username || 'User';
  }

  /** *ngFor trackBy（按 id 稳定追踪）。 */
  trackById = (_index: number, item: { id: number }): number => item.id;

  /** Agent 角色数组（JSON 解析）。 */
  agentRoles(a: AgentRow): string[] {
    if (!a.roles) return [];
    try {
      const parsed = JSON.parse(a.roles);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  /** 相对时间（与 App.timeAgo 行为一致；纯函数避免父级依赖）。 */
  timeAgo(dateStr: string | null | undefined): string {
    if (!dateStr) return '从未';
    const date = new Date(dateStr).getTime();
    if (Number.isNaN(date)) return '从未';
    const diff = Math.floor((Date.now() - date) / 1000);
    if (diff < 60) return `${diff}s 前`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d 前`;
    return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  }
}
