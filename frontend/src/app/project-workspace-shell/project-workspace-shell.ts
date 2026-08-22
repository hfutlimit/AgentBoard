import { Component, ViewEncapsulation, computed, effect, inject } from '@angular/core';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs/operators';
import { TabPaneComponent } from './tab-pane/tab-pane';
import { ProjectDataService } from '../services/project-data.service';
import {
  WorkspaceEntityTabKind,
  WorkspaceSectionTabKind,
  WorkspaceTabsService,
  WorkspaceTab,
} from '../services/workspace-tabs.service';

/**
 * ProjectWorkspaceShellComponent — 项目工作台外壳（2026-08-21 结构调整，v2 修）
 *
 * 之前 v1：菜单 / tab 条用 `<a routerLink>` → Angular router 跳路由 → 触发
 *         app.ts 的 routeSub 调 loadRoute() → 重新拉数据 → 用户感知为"刷新 + 状态丢失"
 * 现在 v2：tab 切换走纯 service 状态（ajax 风格），URL 只用 history.replaceState
 *         静默同步，绝不再触发 router 跳路由。
 *
 * v4：Epic / Proposal 详情成为工作台实体 tab。普通单击由工作台状态接管，
 * Ctrl/Cmd/中键仍通过链接 href 使用浏览器原生新标签行为。
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
  readonly menuItems: ReadonlyArray<{ kind: WorkspaceSectionTabKind; label: string; iconId: string; ariaLabel: string }> = [
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

    effect(() => {
      const epic = this.host.epic();
      if (!epic) return;
      this.tabsService.updateTitle(
        this.tabsService.makeEntityId(epic.project_id, 'epic', epic.id),
        `Epic · ${epic.title}`,
      );
    });

    effect(() => {
      const proposal = this.host.proposalItem();
      if (!proposal) return;
      this.tabsService.updateTitle(
        this.tabsService.makeEntityId(proposal.project_id, 'proposal', proposal.id),
        `提案 · ${proposal.title}`,
      );
    });

    effect(() => {
      const story = this.host.story();
      const projectId = this.host.project()?.id;
      if (!story || !projectId) return;
      this.tabsService.updateTitle(
        this.tabsService.makeEntityId(projectId, 'story', story.id),
        `Story · ${story.title}`,
      );
    });

    effect(() => {
      const task = this.host.task();
      const projectId = this.host.project()?.id;
      if (!task || !projectId) return;
      this.tabsService.updateTitle(
        this.tabsService.makeEntityId(projectId, 'task', task.id),
        `Task · ${task.title}`,
      );
    });

    // 初次挂载：从 router.url 同步当前激活 tab 到 service
    const initial = this.parseRouterUrl(this.router.url);
    if (initial) {
      this.openParsedTab(initial);
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
          this.openParsedTab(cur);
        }
      });
  }

  /** 从 router URL 解析 section tab 或 entity tab。 */
  private parseRouterUrl(url: string): {
    pid: number;
    kind: WorkspaceSectionTabKind | WorkspaceEntityTabKind;
    entityId?: number;
  } | null {
    // URL 形如 /project/<id>/<section>[/...]
    const m = url.match(/^\/project\/(\d+)(?:\/([a-z-]+))?/);
    if (!m) return null;
    const pid = Number(m[1]);
    const section = (m[2] || 'overview') as WorkspaceSectionTabKind;
    if (typeof pid !== 'number' || Number.isNaN(pid)) return null;
    const detail = url.match(/^\/project\/\d+\/(epics|proposals|stories|tasks)\/(\d+)/);
    if (detail) {
      const entityKinds: Record<string, WorkspaceEntityTabKind> = {
        epics: 'epic',
        proposals: 'proposal',
        stories: 'story',
        tasks: 'task',
      };
      return {
        pid,
        kind: entityKinds[detail[1]],
        entityId: Number(detail[2]),
      };
    }
    if (!this.knownKinds.has(section)) return null;
    return { pid, kind: section };
  }

  private openParsedTab(parsed: {
    pid: number;
    kind: WorkspaceSectionTabKind | WorkspaceEntityTabKind;
    entityId?: number;
  }): void {
    if (
      parsed.entityId &&
      (parsed.kind === 'epic' || parsed.kind === 'proposal' || parsed.kind === 'story' || parsed.kind === 'task')
    ) {
      this.tabsService.openEntityTab(parsed.pid, parsed.kind, parsed.entityId);
      return;
    }
    this.tabsService.openTab(parsed.pid, parsed.kind as WorkspaceSectionTabKind);
  }

  /**
   * 点击左侧菜单：**不**触发 router 跳路由，直接调 service + 静默改 URL。
   * 这是 v2 修复的核心 — 之前用 <a routerLink> 导致 app.ts.loadRoute 重拉数据。
   *
   * 仍需主动调 host.loadProjectTab(kind) — 旧版靠 router.navigate 触发
   * app.ts loadRoute 来拉数据，v2 不再走 router，得手动拉。
   */
  onMenuClick(event: MouseEvent, kind: WorkspaceSectionTabKind): void {
    event.preventDefault();
    event.stopPropagation();
    const pid = this.host.project()?.id;
    if (typeof pid !== 'number') return;
    this.tabsService.openTab(pid, kind);
    this.replaceUrl(this.tabsService.activeTab()!);
    this.loadProjectTabIfNeeded(kind, pid);
  }

  /**
   * 点击 tab 条：同样不触发 router 跳路由，纯 service 状态切换 + URL 静默同步。
   */
  onTabClick(event: MouseEvent, tab: WorkspaceTab): void {
    event.preventDefault();
    event.stopPropagation();
    this.tabsService.activateTab(tab.id);
    this.replaceUrl(tab);
    this.loadWorkspaceTab(tab);
  }

  /**
   * 调 host.loadProjectTab 拉 tab 数据（已加载则跳过，内部有 guard）。
   * v2 关键补偿 — 之前靠 router.navigate 顺路触发 app.ts.loadRoute 拉数据。
   */
  private loadProjectTabIfNeeded(kind: WorkspaceSectionTabKind, pid: number): void {
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

  private loadWorkspaceTab(tab: WorkspaceTab): void {
    if (tab.kind === 'epic' && tab.entityId) {
      if (typeof this.host.loadWorkspaceEpicDetail === 'function') {
        void this.host.loadWorkspaceEpicDetail(tab.entityId);
      }
      return;
    }
    if (tab.kind === 'proposal' && tab.entityId) {
      if (typeof this.host.loadWorkspaceProposalDetail === 'function') {
        void this.host.loadWorkspaceProposalDetail(tab.entityId);
      }
      return;
    }
    if (tab.kind === 'story' && tab.entityId) {
      if (typeof this.host.loadWorkspaceStoryDetail === 'function') {
        void this.host.loadWorkspaceStoryDetail(tab.entityId);
      }
      return;
    }
    if (tab.kind === 'task' && tab.entityId) {
      if (typeof this.host.loadWorkspaceTaskDetail === 'function') {
        void this.host.loadWorkspaceTaskDetail(tab.entityId);
      }
      return;
    }
    this.loadProjectTabIfNeeded(tab.kind as WorkspaceSectionTabKind, tab.projectId);
  }

  /** 关闭单个 tab（× 按钮 — 鼠标点击 / 键盘 Enter 共用） */
  onTabClose(event: Event, tab: WorkspaceTab): void {
    event.stopPropagation();
    event.preventDefault();
    this.tabsService.closeTab(tab.id);
    const active = this.tabsService.activeTab();
    if (active) {
      this.replaceUrl(active);
      this.loadWorkspaceTab(active);
    }
  }

  /**
   * 静默改 URL（用 history.replaceState，不触发 Angular router 跳路由）。
   * 让 URL 与 service 状态保持一致，刷新能恢复用户当前激活 tab。
   */
  private replaceUrl(tab: WorkspaceTab): void {
    if (typeof window === 'undefined') return;
    const newPath = tab.kind === 'epic' && tab.entityId
      ? `/project/${tab.projectId}/epics/${tab.entityId}`
      : tab.kind === 'proposal' && tab.entityId
        ? `/project/${tab.projectId}/proposals/${tab.entityId}`
        : tab.kind === 'story' && tab.entityId
          ? `/project/${tab.projectId}/stories/${tab.entityId}`
          : tab.kind === 'task' && tab.entityId
            ? `/project/${tab.projectId}/tasks/${tab.entityId}`
        : `/project/${tab.projectId}/${tab.kind}`;
    if (window.location.pathname === newPath) return;
    window.history.replaceState({}, '', newPath);
  }

  /** 菜单项是否当前激活（用于 sidebar 高亮） */
  isMenuActive(kind: WorkspaceSectionTabKind): boolean {
    const at = this.tabsService.activeTab();
    if (!at) return false;
    if (at.kind === 'epic') return kind === 'epics';
    if (at.kind === 'proposal') return kind === 'proposals';
    if (at.kind === 'story') return kind === 'epics';
    if (at.kind === 'task') return kind === 'backlog';
    return at.kind === kind;
  }

}
