import { Component, ViewEncapsulation, computed, effect, inject } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs/operators';
import { TabPaneComponent } from './tab-pane/tab-pane';
import { ProjectDataService } from '../services/project-data.service';
import { WorkspaceTabsService, WorkspaceTab, WorkspaceTabKind } from '../services/workspace-tabs.service';

/**
 * ProjectWorkspaceShellComponent — 项目工作台外壳（2026-08-21 结构调整）
 *
 * 之前 = 左侧 menu + 单一 <router-outlet>，8 个子路由互斥切换
 * 现在 = 左侧 menu + 顶部 tab 条 + 多 TabPane 容器
 *
 * URL ↔ Tab 状态映射规则（让浏览器前进/后退/直链/刷新全部 work）：
 * - URL 的 section 段（如 /project/123/kanban）是「当前激活 tab」唯一来源
 * - Shell 订阅 NavigationEnd，每次路由变化 → openTab(projectId, kind)
 *   openTab 内部判重：同 (projectId, kind) 已开就只激活，否则新建并激活
 * - Tab 列表（哪些 tab 已开）由 WorkspaceTabsService 维护
 * - 关闭 tab 只从 service 移除，不动 URL（用户停留当前 tab）
 *
 * 同 (projectId, kind) 至多 1 个 tab；切项目 → service 自动清空 tab 列表
 */
@Component({
  selector: 'app-project-workspace-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, TabPaneComponent],
  templateUrl: './project-workspace-shell.html',
  styleUrl: './project-workspace-shell.css',
  encapsulation: ViewEncapsulation.None,
})
export class ProjectWorkspaceShellComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();
  readonly tabsService = inject(WorkspaceTabsService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  /** 左侧菜单 8 项（顺序固定：概览 → 设置），用于渲染 sidebar nav */
  readonly menuItems: ReadonlyArray<{ kind: WorkspaceTabKind; label: string; iconId: string; ariaLabel: string }> = [
    { kind: 'overview',  label: '概览',        iconId: 'i-home',     ariaLabel: '概览' },
    { kind: 'kanban',    label: '看板',        iconId: 'i-board',    ariaLabel: '看板' },
    { kind: 'epics',     label: 'Epics',      iconId: 'i-flag',     ariaLabel: 'Epics' },
    { kind: 'backlog',   label: '工作项',      iconId: 'i-list',     ariaLabel: '工作项' },
    { kind: 'proposals', label: '提案',        iconId: 'i-message',  ariaLabel: '提案' },
    { kind: 'documents', label: '文档',        iconId: 'i-file',     ariaLabel: '文档' },
    { kind: 'members',   label: '成员与 Agents', iconId: 'i-users',   ariaLabel: '成员与 Agents' },
    { kind: 'settings',  label: '设置',        iconId: 'i-settings', ariaLabel: '设置' },
  ];

  readonly projectMonogram = computed(() => {
    const p = this.host.project();
    return (p?.name || p?.key || 'AB').slice(0, 2).toUpperCase();
  });
  readonly projectName = computed(() => this.host.project()?.name || '未选择项目');
  readonly projectMeta = computed(() => this.host.project()?.key || '项目工作台');
  readonly onlineAgentCount = computed(() => this.host.agents().filter((agent: any) => agent.online).length);

  readonly tabs = this.tabsService.tabs;
  readonly activeId = this.tabsService.activeId;
  readonly isEmpty = this.tabsService.isEmpty;

  constructor() {
    // 切项目 → 重置 tab 列表（用户回答"切项目清空"），空状态时自动开概览
    effect(() => {
      const pid = this.host.project()?.id;
      if (typeof pid !== 'number') return;
      this.tabsService.setProject(pid);
      if (this.tabsService.isEmpty()) {
        this.tabsService.openTab(pid, 'overview');
      }
    });

    // URL → Tab 同步：每次 NavigationEnd 解析 section 段，调 openTab
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((event) => {
        this.syncFromUrl(event.urlAfterRedirects);
      });
  }

  /** 从 URL 解析 section 并 openTab */
  private syncFromUrl(url: string): void {
    // URL 形如 /project/<id>/<section>[/...]
    const m = url.match(/^\/project\/(\d+)(?:\/([a-z-]+))?/);
    if (!m) return;
    const pid = Number(m[1]);
    const section = m[2] || 'overview';
    if (!this.isKnownKind(section)) return;
    if (typeof pid !== 'number') return;
    this.tabsService.openTab(pid, section as WorkspaceTabKind);
  }

  private readonly knownKinds: Set<string> = new Set([
    'overview', 'kanban', 'epics', 'backlog', 'proposals', 'documents', 'members', 'settings',
  ]);
  private isKnownKind(s: string): boolean {
    return this.knownKinds.has(s);
  }

  /**
   * 点击左侧菜单：让 router 改 URL，NavigationEnd 触发 syncFromUrl
   * （不直接调 openTab —— 保持 URL = 状态 唯一来源，行为统一）
   */
  onMenuClick(event: MouseEvent, kind: WorkspaceTabKind): void {
    // <a routerLink> 默认会 navigate；这里只需 stopPropagation 防止冒泡
    event.stopPropagation();
    // 让 routerLink 自然处理；这里不再额外 openTab
    void kind; // suppress unused
  }

  /**
   * 点击 tab 条上的 tab 项：让 router 改 URL，NavigationEnd 触发 syncFromUrl
   * （同样保持 URL 为唯一来源）
   */
  onTabClick(event: MouseEvent, tab: WorkspaceTab): void {
    event.stopPropagation();
    void tab;
  }

  /** 关闭单个 tab（× 按钮 — 鼠标点击 / 键盘 Enter 共用） */
  onTabClose(event: Event, tab: WorkspaceTab): void {
    event.stopPropagation();
    event.preventDefault();
    this.tabsService.closeTab(tab.id);
  }

  /** 菜单项是否当前激活（用于 sidebar 高亮） */
  isMenuActive(kind: WorkspaceTabKind): boolean {
    const at = this.tabsService.activeTab();
    return !!at && at.kind === kind;
  }
}
