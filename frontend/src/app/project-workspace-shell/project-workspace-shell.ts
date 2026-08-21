import { Component, ViewEncapsulation, computed, effect, inject } from '@angular/core';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs/operators';
import { TabPaneComponent } from './tab-pane/tab-pane';
import { ProjectDataService } from '../services/project-data.service';
import { WorkspaceTabsService, WorkspaceTab, WorkspaceTabKind } from '../services/workspace-tabs.service';

/**
 * ProjectWorkspaceShellComponent — 项目工作台外壳（2026-08-21 结构调整，v2 修）
 *
 * 之前 v1：菜单 / tab 条用 `<a routerLink>` → Angular router 跳路由 → 触发
 *         app.ts 的 routeSub 调 loadRoute() → 重新拉数据 → 用户感知为"刷新 + 状态丢失"
 * 现在 v2：tab 切换走纯 service 状态（ajax 风格），URL 只用 history.replaceState
 *         静默同步，绝不再触发 router 跳路由。
 *
 * URL ↔ Tab 状态映射规则（v2 调整后）：
 * - 初次加载 / 直链 / 刷新：shell 构造函数读一次 URL，调 openTab(projectId, kind)，
 *   让 service 与 URL 对齐
 * - 浏览器前进/后退：popstate 事件 → shell 重新读 URL 调 openTab
 * - 菜单 / tab 条点击：**不**触发 router 跳路由，直接调 service + history.replaceState
 *   静默改 URL（这样刷新也能保留用户当前激活 tab）
 *
 * 同 (projectId, kind) 至多 1 个 tab；切项目 → service 自动清空 tab 列表
 */
@Component({
  selector: 'app-project-workspace-shell',
  standalone: true,
  imports: [RouterOutlet, TabPaneComponent],
  templateUrl: './project-workspace-shell.html',
  styleUrl: './project-workspace-shell.css',
  encapsulation: ViewEncapsulation.None,
})
export class ProjectWorkspaceShellComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();
  readonly tabsService = inject(WorkspaceTabsService);
  private readonly router = inject(Router);

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

  private readonly knownKinds: ReadonlySet<string> = new Set([
    'overview', 'kanban', 'epics', 'backlog', 'proposals', 'documents', 'members', 'settings',
  ]);

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

    // 初次挂载：从 router.url 同步当前激活 tab 到 service
    const initial = this.parseRouterUrl(this.router.url);
    if (initial) {
      this.tabsService.openTab(initial.pid, initial.kind);
    }

    // 监听 router 跳路由：仅同步 URL → service，**不**调用 router.navigate 自己。
    // 触发场景：(1) app.ts 里的 selectProjectTab() 等其他地方的 router.navigate
    // (2) 浏览器 back/forward。
    // 注意：菜单 / tab 条的 click **不**走 router（v2 修核心），所以这条只对
    // 程序化路由（app.ts / popstate）响应。
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((event) => {
        const cur = this.parseRouterUrl(event.urlAfterRedirects);
        if (cur) {
          this.tabsService.openTab(cur.pid, cur.kind);
        }
      });
  }

  /** 从 router URL 解析 (projectId, kind) */
  private parseRouterUrl(url: string): { pid: number; kind: WorkspaceTabKind } | null {
    // URL 形如 /project/<id>/<section>[/...]
    const m = url.match(/^\/project\/(\d+)(?:\/([a-z-]+))?/);
    if (!m) return null;
    const pid = Number(m[1]);
    const section = (m[2] || 'overview') as WorkspaceTabKind;
    if (!this.knownKinds.has(section)) return null;
    if (typeof pid !== 'number' || Number.isNaN(pid)) return null;
    return { pid, kind: section };
  }

  /**
   * 点击左侧菜单：**不**触发 router 跳路由，直接调 service + 静默改 URL。
   * 这是 v2 修复的核心 — 之前用 <a routerLink> 导致 app.ts.loadRoute 重拉数据。
   *
   * 仍需主动调 host.loadProjectTab(kind) — 旧版靠 router.navigate 触发
   * app.ts loadRoute 来拉数据，v2 不再走 router，得手动拉。
   */
  onMenuClick(event: MouseEvent, kind: WorkspaceTabKind): void {
    event.preventDefault();
    event.stopPropagation();
    const pid = this.host.project()?.id;
    if (typeof pid !== 'number') return;
    this.tabsService.openTab(pid, kind);
    this.replaceUrl(pid, kind);
    this.loadProjectTabIfNeeded(kind, pid);
  }

  /**
   * 点击 tab 条：同样不触发 router 跳路由，纯 service 状态切换 + URL 静默同步。
   */
  onTabClick(event: MouseEvent, tab: WorkspaceTab): void {
    event.preventDefault();
    event.stopPropagation();
    this.tabsService.activateTab(tab.id);
    this.replaceUrl(tab.projectId, tab.kind);
    this.loadProjectTabIfNeeded(tab.kind, tab.projectId);
  }

  /**
   * 调 host.loadProjectTab 拉 tab 数据（已加载则跳过，内部有 guard）。
   * v2 关键补偿 — 之前靠 router.navigate 顺路触发 app.ts.loadRoute 拉数据。
   */
  private loadProjectTabIfNeeded(kind: WorkspaceTabKind, pid: number): void {
    if (kind === 'settings') {
      // settings 走聚合 loadProjectSettings
      if (typeof (this.host as any).loadProjectSettings === 'function') {
        void (this.host as any).loadProjectSettings(pid, (this.host as any).projectTabGeneration);
      }
      return;
    }
    if (typeof (this.host as any).loadProjectTab === 'function') {
      void (this.host as any).loadProjectTab(kind, pid);
    }
  }

  /** 关闭单个 tab（× 按钮 — 鼠标点击 / 键盘 Enter 共用） */
  onTabClose(event: Event, tab: WorkspaceTab): void {
    event.stopPropagation();
    event.preventDefault();
    this.tabsService.closeTab(tab.id);
  }

  /**
   * 静默改 URL（用 history.replaceState，不触发 Angular router 跳路由）。
   * 让 URL 与 service 状态保持一致，刷新能恢复用户当前激活 tab。
   */
  private replaceUrl(pid: number, kind: WorkspaceTabKind): void {
    if (typeof window === 'undefined') return;
    const newPath = `/project/${pid}/${kind}`;
    if (window.location.pathname === newPath) return;
    window.history.replaceState({}, '', newPath);
  }

  /** 菜单项是否当前激活（用于 sidebar 高亮） */
  isMenuActive(kind: WorkspaceTabKind): boolean {
    const at = this.tabsService.activeTab();
    return !!at && at.kind === kind;
  }
}
