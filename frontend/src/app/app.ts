import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, ViewEncapsulation, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';
import { DOCUMENT } from '@angular/common';
import { Inject } from '@angular/core';
import { filter } from 'rxjs/operators';

import { ApiService, AUTH_EXPIRED_EVENT, OFFLINE_QUEUE_FLUSH_EVENT, perfTracker, ApiMetric } from './api.service';
import { LoginComponent } from './login/login';
import { AgentSchedule, Attachment, AuditLog, Comment, Epic, ItemType, Notification, Priority, Project, ProjectMember, ProjectStats, Sprint, SprintStatus, Status, Story, Task, TaskDependencies, WebhookConfig } from './models';

type ViewKind = 'home' | 'projects' | 'project' | 'epic' | 'story' | 'task' | 'sprint' | 'admin' | 'settings' | 'not-found';
type CreateKind = 'project' | 'epic' | 'story' | 'task';

interface CreateModal {
  kind: CreateKind;
  parentId?: number;
  projectId?: number;
}

@Component({
  selector: 'app-root',
  imports: [CommonModule, FormsModule, RouterLink, RouterOutlet, LoginComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
  encapsulation: ViewEncapsulation.None,
})
export class App implements OnInit, OnDestroy {
  readonly projects = signal<Project[]>([]);
  readonly recentProjects = signal<Project[]>([]);
  readonly epics = signal<Epic[]>([]);
  readonly stories = signal<Story[]>([]);
  readonly tasks = signal<Task[]>([]);
  readonly comments = signal<Comment[]>([]);
  readonly sprints = signal<Sprint[]>([]);
  readonly sprint = signal<Sprint | null>(null);
  readonly sprintTasks = signal<Task[]>([]);
  readonly backlogTasks = signal<Task[]>([]);
  readonly sprintBurndown = signal<any>(null);
  readonly project = signal<Project | null>(null);
  readonly epic = signal<Epic | null>(null);
  readonly story = signal<Story | null>(null);
  readonly task = signal<Task | null>(null);
  readonly view = signal<ViewKind>('home');
  readonly loading = signal(true);
  readonly error = signal('');
  readonly search = signal('');
  readonly sidebarOpen = signal(true);
  readonly boardMode = signal(localStorage.getItem('agentboard_story_view') === 'board');
  readonly authVisible = signal(!localStorage.getItem('agentboard_token'));
  readonly authMode = signal<'login' | 'register'>('login');
  readonly currentUser = signal(localStorage.getItem('agentboard_user') || '');
  readonly toastMessage = signal('');
  readonly toastType = signal<'success' | 'error'>('success');
  // Epic 24 Story 24.2: Toast 增强 - 多 toasts 支持
  private _toastCounter = 0;
  readonly toasts = signal<{ id: number; message: string; type: 'success' | 'error' }[]>([]);
  readonly modal = signal<CreateModal | null>(null);
  readonly submitting = signal(false);
  readonly activeTab = signal<'epics' | 'sprints' | 'backlog' | 'settings' | 'members' | 'stats' | 'schedules'>('epics');
  readonly members = signal<ProjectMember[]>([]);
  readonly notifications = signal<Notification[]>([]);
  readonly unreadCount = signal(0);
  readonly showNotifications = signal(false);
  readonly showUserMenu = signal(false);
  readonly projectStats = signal<ProjectStats | null>(null);
  readonly schedules = signal<AgentSchedule[]>([]);
  readonly statsMaxCreated = computed(() => {
    const stats = this.projectStats();
    if (!stats) return 1;
    return Math.max(...(stats.daily_created.map(d => d.count) || [1]), 1);
  });
  readonly statsMaxDone = computed(() => {
    const stats = this.projectStats();
    if (!stats) return 1;
    return Math.max(...(stats.daily_done.map(d => d.count) || [1]), 1);
  });
  readonly isOwner = signal(false);
  readonly isAdmin = signal(false);
  readonly healthStatus = signal<'ok' | 'error' | 'unknown'>('unknown');
  readonly healthDetail = signal<{ status: string; database: string; version: string; timestamp: string } | null>(null);
  readonly showHealth = signal(false);
  readonly offlineBanner = signal(false);  // Task 402: API 离线检测
  // Epic 21 Story 21.4: 离线状态详细提示
  readonly offlineQueueCount = signal(0);
  readonly appError = signal<string | null>(null);  // Task 431: 错误边界
  readonly attachments = signal<Attachment[]>([]);
  readonly adminUsers = signal<any[]>([]);
  readonly adminProjects = signal<any[]>([]);
  readonly selectedTasks = signal<Set<number>>(new Set());
  readonly bulkActionTarget = signal<string | null>(null); // 'status' | 'delete' | null
  // Epic 21 Story 21.3: 批量操作进度跟踪
  readonly bulkProgress = signal<{ current: number; total: number; message: string } | null>(null);
  readonly focusedTaskIndex = signal<number>(-1);
  readonly exportDropdownOpen = signal(false);
  // Epic 21 Story 21.4: 组件级错误边界状态
  readonly hasError = signal(false);
  readonly errorMessage = signal('');
  readonly lastSelectedTaskId = signal<number | null>(null); // Shift+点击多选支持

  // Epic 26 Task 702: 搜索历史记录
  readonly searchHistory = signal<{ query: string; timestamp: number }[]>([]);
  readonly showSearchHistory = signal(false);

  // Epic 26 Task 704: 任务详情相邻导航
  readonly prevTask = signal<Task | null>(null);
  readonly nextTask = signal<Task | null>(null);
  // Epic 25: API Keys
  readonly apiKeys = signal<any[]>([]);
  readonly newKeyName = signal('');
  readonly newKeyPerms = signal('');
  readonly keyModalVisible = signal(false);
  // Task 714: 虚拟滚动 - 列表分页加载（初始显示数量）
  readonly taskPageSize = signal(50);
  readonly taskPageCount = signal(1);
  // Task 716: 全局快捷键面板
  readonly showShortcuts = signal(false);
  readonly createdKeyPlaintext = signal('');
  // Epic 22: 任务依赖
  readonly taskDependencies = signal<TaskDependencies | null>(null);
  // Epic 22: Webhooks
  readonly webhooks = signal<WebhookConfig[]>([]);
  // Epic 22: 审计日志
  readonly auditLogs = signal<AuditLog[]>([]);
  // Task 708: 性能指标显示
  readonly apiMetrics = signal<ApiMetric[]>([]);
  readonly avgApiDuration = signal<number>(0);
  readonly apiSuccessRate = signal<number>(100);
  readonly pageLoadTime = signal<number>(0);
  readonly showPerformance = signal(false);
  readonly statuses: Status[] = [
    'backlog',
    'todo',
    'in_progress',
    'in_review',
    'verifying',
    'done',
  ];
  readonly priorities: Priority[] = ['highest', 'high', 'medium', 'low', 'lowest'];

  readonly visibleProjects = computed(() =>
    this.match(this.projects(), (p) => `${p.name} ${p.key || ''} ${p.description}`),
  );
  readonly visibleEpics = computed(() =>
    this.match(this.epics(), (e) => `${e.title} ${e.description}`),
  );
  readonly visibleStories = computed(() =>
    this.match(this.stories(), (s) => `${s.title} ${s.description}`),
  );
  // Task 602: 高级筛选面板 - 状态/优先级过滤
  readonly filterStatus = signal('');
  readonly filterPriority = signal('');
  readonly visibleTasks = computed(() => {
    const search = this.match(this.tasks(), (t) => `${t.title} ${t.description} ${t.spec}`);
    const status = this.filterStatus();
    const priority = this.filterPriority();
    return search.filter((t: Task) =>
      (!status || t.status === status) && (!priority || t.priority === priority)
    );
  });
  readonly doneTasks = computed(() => this.tasks().filter((t) => t.status === 'done').length);

  private routeSub?: Subscription;
  private toastTimer?: ReturnType<typeof setTimeout>;
  private healthTimer?: ReturnType<typeof setInterval>;   // Task 400: 健康检查轮询
  private notifTimer?: ReturnType<typeof setInterval>;    // Task 401: 通知轮询
  private readonly colorScheme = window.matchMedia?.('(prefers-color-scheme: dark)');
  private readonly handleColorSchemeChange = (event: MediaQueryListEvent): void => {
    if (!localStorage.getItem('agentboard_theme')) {
      this.applyTheme(event.matches ? 'dark' : 'light');
    }
  };
  private readonly handleAuthExpired = (): void => {
    this.currentUser.set('');
    this.isAdmin.set(false);
    this.isOwner.set(false);
    this.showLogin();
    this.notify('登录已失效，请重新登录', 'error');
  };
  // Task 402: 网络离线检测
  // Epic 21 Story 21.4: 优化离线状态提示
  private readonly handleOnline = (): void => {
    this.offlineBanner.set(false);
    this.offlineQueueCount.set(0);
    // Task 472: flush offline queue when back online
    const queue = (() => {
      try { return JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]'); }
      catch { return []; }
    })();
    if (queue.length > 0) {
      localStorage.removeItem('agentboard_offline_queue');
      this.notify(`已恢复网络，正在重发 ${queue.length} 个离线操作…`);
    }
  };
  private readonly handleOffline = (): void => {
    this.offlineBanner.set(true);
    // 同步更新离线队列计数
    try {
      const queue = JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]');
      this.offlineQueueCount.set(queue.length);
    } catch { this.offlineQueueCount.set(0); }
  };

  // Epic 21 Story 21.4: 全局错误边界处理器
  private readonly handleGlobalError = (event: ErrorEvent): void => {
    const msg = event.message || '发生了未知错误';
    // 忽略某些常见的非关键错误
    if (msg.includes('ResizeObserver') || msg.includes('ResizeObserver loop')) return;
    this.appError.set(msg);
    // 5 秒后自动消失（除非是严重错误）
    setTimeout(() => {
      if (this.appError() === msg) this.appError.set(null);
    }, 5000);
  };

  private readonly handleUnhandledRejection = (event: PromiseRejectionEvent): void => {
    // 忽略离线队列相关错误
    if (event.reason?.message?.includes('离线')) {
      return;
    }
    const msg = event.reason?.message || '异步错误';
    this.appError.set(msg);
    setTimeout(() => {
      if (this.appError() === msg) this.appError.set(null);
    }, 5000);
    console.error('[UnhandledRejection]', event.reason);
  };

  // Story 21.4: 错误边界重置
  resetErrorBoundary(): void {
    this.hasError.set(false);
    this.errorMessage.set('');
    this.appError.set(null);
  }

  constructor(
    readonly api: ApiService,
    private readonly router: Router,
    @Inject(DOCUMENT) private readonly document: Document,
  ) {}

  ngOnInit(): void {
    // Task 708: 记录页面加载时间
    this.pageLoadTime.set(performance.now());
    window.addEventListener(AUTH_EXPIRED_EVENT, this.handleAuthExpired);
    window.addEventListener('online', this.handleOnline);    // Task 402: 离线检测
    window.addEventListener('offline', this.handleOffline);
    window.addEventListener('error', this.handleGlobalError); // Task 431: 错误边界
    const saved = localStorage.getItem('agentboard_theme');
    // 优先使用用户偏好，其次跟随系统
    const theme = saved || (this.colorScheme?.matches ? 'dark' : 'light');
    this.applyTheme(theme);
    this.loadRecentProjects();
    // Listen for system theme changes
    this.colorScheme?.addEventListener('change', this.handleColorSchemeChange);
    // Epic 21 Story 21.4: 全局错误处理
    window.addEventListener('error', this.handleGlobalError);
    window.addEventListener('unhandledrejection', this.handleUnhandledRejection);
    // 启动时校验已有 token，失败则清除并显示登录
    void this.validateAuth();
    this.routeSub = this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.loadRoute());
    void this.loadRoute();
    // Task 400: 健康检查轮询（默认开启，可通过 localStorage 关闭）
    if (localStorage.getItem('agentboard_health_poll') !== 'disabled') {
      void this.checkHealth();
      this.healthTimer = setInterval(() => {
        if (localStorage.getItem('agentboard_health_poll') !== 'disabled') {
          void this.checkHealth();
        }
      }, 60000); // 60s
    }
    // Task 401: 通知轮询（每 60s）
    this.notifTimer = setInterval(() => {
      if (this.authVisible()) return;
      void this.loadNotifications();
    }, 60000);
    // Story 21.4: 初始化时更新离线队列计数
    try {
      const queue = JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]');
      this.offlineQueueCount.set(queue.length);
    } catch { this.offlineQueueCount.set(0); }
    // Epic 26 Task 702: 加载搜索历史记录
    this.loadSearchHistory();
    // Task 716/711: 全局快捷键 - '?' 键打开快捷键帮助，Ctrl+A 全选，Del 删除选中
    window.addEventListener('keydown', (e: KeyboardEvent) => {
      if (this.isInputFocused()) return;
      if (e.key === '?') {
        e.preventDefault();
        this.toggleShortcuts();
      }
      // Task 711: Ctrl+A 全选当前列表任务
      if (e.ctrlKey && e.key === 'a') {
        e.preventDefault();
        this.selectAllTasks();
      }
      // Task 711: Del 删除选中任务
      if (e.key === 'Delete' && this.selectedTasks().size > 0) {
        e.preventDefault();
        this.bulkDelete();
      }
    });
  }

  // Task 716: 判断当前焦点是否在输入元素上
  private isInputFocused(): boolean {
    const el = document.activeElement;
    return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
  }

  // Epic 26 Task 702: 加载搜索历史记录
  private loadSearchHistory(): void {
    try {
      const stored = localStorage.getItem('agentboard_search_history');
      if (stored) {
        this.searchHistory.set(JSON.parse(stored));
      }
    } catch { this.searchHistory.set([]); }
  }

  // Epic 26 Task 702: 保存搜索历史记录
  saveSearchHistory(query: string): void {
    if (!query.trim()) return;
    try {
      const KEY = 'agentboard_search_history';
      const MAX = 10;
      let history = this.searchHistory();
      // Remove duplicate if exists
      history = history.filter(h => h.query !== query);
      // Add new query at the beginning
      history.unshift({ query, timestamp: Date.now() });
      // Keep only MAX items
      history = history.slice(0, MAX);
      this.searchHistory.set(history);
      localStorage.setItem(KEY, JSON.stringify(history));
    } catch { /* ignore */ }
  }

  // Epic 26 Task 702: 清除单条搜索历史
  removeSearchHistoryItem(query: string): void {
    try {
      const history = this.searchHistory().filter(h => h.query !== query);
      this.searchHistory.set(history);
      localStorage.setItem('agentboard_search_history', JSON.stringify(history));
    } catch { /* ignore */ }
  }

  // Epic 26 Task 702: 清除所有搜索历史
  clearSearchHistory(): void {
    this.searchHistory.set([]);
    localStorage.removeItem('agentboard_search_history');
    this.showSearchHistory.set(false);
  }

  // Epic 26 Task 702: 选择历史记录项
  selectSearchHistory(query: string): void {
    this.search.set(query);
    this.showSearchHistory.set(false);
  }

  // Epic 26 Task 703: 高亮搜索关键词
  highlightSearch(text: string, query: string): string {
    if (!query.trim() || !text) return text;
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escaped})`, 'gi');
    return text.replace(regex, '<mark class="search-highlight">$1</mark>');
  }

  // Epic 26 Task 704: 计算相邻任务
  private updatePrevNextTasks(currentTaskId: number): void {
    const allTasks = this.visibleTasks();
    const currentIndex = allTasks.findIndex(t => t.id === currentTaskId);
    if (currentIndex >= 0) {
      this.prevTask.set(currentIndex > 0 ? allTasks[currentIndex - 1] : null);
      this.nextTask.set(currentIndex < allTasks.length - 1 ? allTasks[currentIndex + 1] : null);
    }
  }

  async checkHealth(): Promise<void> {
    try {
      const health = await firstValueFrom(this.api.getHealth());
      this.healthStatus.set(health.status === 'ok' && health.database === 'ok' ? 'ok' : 'error');
      this.healthDetail.set(health);
    } catch {
      this.healthStatus.set('error');
      this.healthDetail.set(null);
    }
  }

  toggleHealth(): void {
    this.showHealth.set(!this.showHealth());
    if (this.showHealth()) {
      void this.checkHealth();
      // Task 708: 更新性能指标
      this.updatePerformanceMetrics();
    }
  }

  // Task 708: 更新性能指标
  updatePerformanceMetrics(): void {
    this.apiMetrics.set(perfTracker.getRecentMetrics(10));
    this.avgApiDuration.set(Math.round(perfTracker.getAverageDuration()));
    this.apiSuccessRate.set(Math.round(perfTracker.getSuccessRate()));
  }

  // Task 708: 格式化性能指标时间
  formatMetricTime(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  // Task 708: 格式化页面加载时间
  formatLoadTime(): string {
    const ms = this.pageLoadTime();
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  // Task 400: 切换健康检查轮询
  toggleHealthPoll(): void {
    const current = localStorage.getItem('agentboard_health_poll');
    if (current === 'disabled') {
      localStorage.removeItem('agentboard_health_poll');
      this.notify('健康检查轮询已开启（每 60s）');
    } else {
      localStorage.setItem('agentboard_health_poll', 'disabled');
      this.notify('健康检查轮询已关闭');
    }
  }

  isHealthPollEnabled(): boolean {
    return localStorage.getItem('agentboard_health_poll') !== 'disabled';
  }

  /** 启动时验证 localStorage 中的 token，有效则恢复登录态，无效则清除并进入登录页 */
  private async validateAuth(): Promise<void> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) {
      this.showLogin();
      return;
    }
    try {
      const me = await firstValueFrom(this.api.me());
      this.currentUser.set(me.username);
      this.isAdmin.set(me.is_admin ?? false);
      localStorage.setItem('agentboard_user', me.username);
      localStorage.setItem('agentboard_is_admin', String(me.is_admin ?? false));
    } catch {
      // token 失效，清除并显示登录
      localStorage.removeItem('agentboard_token');
      localStorage.removeItem('agentboard_user');
      localStorage.removeItem('agentboard_is_admin');
      this.currentUser.set('');
      this.isAdmin.set(false);
      this.openAuth('login');
    }
  }

  // Task 431: 手动关闭错误提示
  dismissError(): void {
    this.appError.set(null);
  }

  ngOnDestroy(): void {
    window.removeEventListener(AUTH_EXPIRED_EVENT, this.handleAuthExpired);
    window.removeEventListener('online', this.handleOnline);    // Task 402
    window.removeEventListener('offline', this.handleOffline);
    window.removeEventListener('error', this.handleGlobalError);
    window.removeEventListener('unhandledrejection', this.handleUnhandledRejection);
    this.colorScheme?.removeEventListener('change', this.handleColorSchemeChange);
    this.routeSub?.unsubscribe();
    if (this.toastTimer) clearTimeout(this.toastTimer);
    if (this.healthTimer) clearInterval(this.healthTimer);    // Task 400
    if (this.notifTimer) clearInterval(this.notifTimer);     // Task 401
  }

  private match<T>(items: T[], text: (item: T) => string): T[] {
    const query = this.search().trim().toLocaleLowerCase();
    return query ? items.filter((item) => text(item).toLocaleLowerCase().includes(query)) : items;
  }

  private async loadRoute(): Promise<void> {
    // 未登录时不加载任何业务数据，由独立登录页接管
    if (this.authVisible()) return;
    this.loading.set(true);
    this.error.set('');
    const path = this.router.url.split('?')[0].replace(/^\//, '');
    const [kind = '', rawId] = path.split('/');
    const id = Number(rawId);
    // 已登录用户直接访问 /login 时回首页
    if (kind === 'login') {
      await this.router.navigateByUrl('/');
      return;
    }
    try {
      await this.loadProjects();
      if (!kind) {
        this.view.set('home');
        await this.loadDashboard();
      } else if (kind === 'projects') {
        this.view.set('projects');
      } else if (kind === 'project' && id > 0) {
        this.view.set('project');
        this.activeTab.set('epics');
        const [project, epics] = await Promise.all([
          firstValueFrom(this.api.getProject(id)),
          firstValueFrom(this.api.listEpics(id)),
        ]);
        this.project.set(project);
        this.epics.set(epics);
        this.trackRecentProject(project);
        await Promise.all([
          this.loadSprints(id),
          this.loadBacklog(id),
          this.loadMembers(id),
          this.loadProjectStats(id),
          this.loadSchedules(id),
        ]);
        await this.checkProjectOwner(id);
      } else if (kind === 'epic' && id > 0) {
        this.view.set('epic');
        const [epic, stories] = await Promise.all([
          firstValueFrom(this.api.getEpic(id)),
          firstValueFrom(this.api.listStories(id)),
        ]);
        this.epic.set(epic);
        this.stories.set(stories);
        this.project.set(await firstValueFrom(this.api.getProject(epic.project_id)));
      } else if (kind === 'story' && id > 0) {
        this.view.set('story');
        const [story, tasks] = await Promise.all([
          firstValueFrom(this.api.getStory(id)),
          firstValueFrom(this.api.listTasks(id)),
        ]);
        this.story.set(story);
        this.tasks.set(tasks);
        const epic = await firstValueFrom(this.api.getEpic(story.epic_id));
        this.epic.set(epic);
        this.project.set(await firstValueFrom(this.api.getProject(epic.project_id)));
      } else if (kind === 'task' && id > 0) {
        this.view.set('task');
        const [task, comments] = await Promise.all([
          firstValueFrom(this.api.getTask(id)),
          firstValueFrom(this.api.listComments(id)),
        ]);
        this.task.set(task);
        this.comments.set(comments);
        await this.loadAttachments(id);
        if (task.story_id) {
          const story = await firstValueFrom(this.api.getStory(task.story_id));
          this.story.set(story);
          const epic = await firstValueFrom(this.api.getEpic(story.epic_id));
          this.epic.set(epic);
          const project = await firstValueFrom(this.api.getProject(epic.project_id));
          this.project.set(project);
          await this.loadSprints(project.id);
        } else {
          this.project.set(await firstValueFrom(this.api.getProject(task.project_id)));
          await this.loadSprints(task.project_id);
        }
        // Epic 26 Task 704: 更新相邻任务导航
        this.updatePrevNextTasks(id);
      } else if (kind === 'sprint' && id > 0) {
        this.view.set('sprint');
        const [sprint, tasks] = await Promise.all([
          firstValueFrom(this.api.getSprint(id)),
          firstValueFrom(this.api.listSprintTasks(id)),
        ]);
        this.sprint.set(sprint);
        this.sprintTasks.set(tasks);
        this.project.set(await firstValueFrom(this.api.getProject(sprint.project_id)));
        await this.loadSprintBurndown(id);
      } else if (kind === 'admin') {
        const me = await this.adminMe();
        if (!me?.is_admin) {
          this.router.navigateByUrl('/');
          return;
        }
        this.view.set('admin');
        await this.loadAdminData();
      } else if (kind === 'settings') {
        if (!localStorage.getItem('agentboard_token')) {
          this.router.navigateByUrl('/login');
          return;
        }
        this.view.set('settings');
        await this.loadApiKeys();
      } else {
        this.view.set('not-found');
      }
    } catch (error) {
      this.error.set(this.message(error));
    } finally {
      this.loading.set(false);
    }
  }

  private async loadProjects(): Promise<void> {
    const result = await firstValueFrom(this.api.listProjects());
    this.projects.set(Array.isArray(result) ? result : (result.items || []));
  }

  private loadRecentProjects(): void {
    try {
      const stored = localStorage.getItem('agentboard_recent_projects');
      if (stored) {
        const ids: number[] = JSON.parse(stored);
        // Will be filtered when projects are loaded
        this.recentProjects.set([]);
      }
    } catch { /* ignore */ }
  }

  trackRecentProject(project: Project): void {
    try {
      const KEY = 'agentboard_recent_projects';
      const MAX = 5;
      let ids: number[] = [];
      const stored = localStorage.getItem(KEY);
      if (stored) ids = JSON.parse(stored);
      ids = ids.filter(id => id !== project.id);
      ids.unshift(project.id);
      ids = ids.slice(0, MAX);
      localStorage.setItem(KEY, JSON.stringify(ids));
      // Filter projects to get recent ones
      const recent = ids.map(id => this.projects().find(p => p.id === id)).filter(Boolean) as Project[];
      this.recentProjects.set(recent);
    } catch { /* ignore */ }
  }

  private async loadDashboard(): Promise<void> {
    const allEpics = (
      await Promise.all(
        this.projects().map((project) => firstValueFrom(this.api.listEpics(project.id))),
      )
    ).flat();
    this.epics.set(allEpics);
    const allStories = (
      await Promise.all(allEpics.map((epic) => firstValueFrom(this.api.listStories(epic.id))))
    ).flat();
    this.stories.set(allStories);
    const allTasks = (
      await Promise.all(allStories.map((story) => firstValueFrom(this.api.listTasks(story.id))))
    ).flat();
    this.tasks.set(allTasks);
  }

  async refresh(): Promise<void> {
    await this.loadRoute();
  }

  openAuth(mode: 'login' | 'register' = 'login'): void {
    this.showLogin(mode);
  }

  /** 进入独立登录页（全屏，非弹窗） */
  private showLogin(mode: 'login' | 'register' = 'login'): void {
    this.authMode.set(mode);
    this.authVisible.set(true);
    if (this.router.url !== '/login') {
      void this.router.navigateByUrl('/login');
    }
  }

  closeAuth(): void {
    this.authVisible.set(false);
  }

  async authenticate(username: string, password: string): Promise<void> {
    this.submitting.set(true);
    try {
      const result = await firstValueFrom(
        this.authMode() === 'register'
          ? this.api.register(username.trim(), password)
          : this.api.login(username.trim(), password),
      );
      localStorage.setItem('agentboard_token', result.token);
      localStorage.setItem('agentboard_user', result.username);
      localStorage.setItem('agentboard_is_admin', String(result.is_admin ?? false));
      this.currentUser.set(result.username);
      this.isAdmin.set(result.is_admin ?? false);
      this.authVisible.set(false);
      this.notify(this.authMode() === 'register' ? '注册成功，已登录' : '登录成功');
      if (this.router.url.startsWith('/login')) {
        await this.router.navigateByUrl('/');
      } else {
        await this.loadRoute();
      }
    } catch (error) {
      this.notify(
        `${this.authMode() === 'register' ? '注册' : '登录'}失败：${this.message(error)}`,
        'error',
      );
    } finally {
      this.submitting.set(false);
    }
  }

  logout(): void {
    localStorage.removeItem('agentboard_token');
    localStorage.removeItem('agentboard_user');
    localStorage.removeItem('agentboard_is_admin');
    this.currentUser.set('');
    this.isAdmin.set(false);
    this.isOwner.set(false);
    this.showLogin('login');
    this.notify('已退出登录');
  }

  openCreate(kind: CreateKind, parentId?: number, projectId?: number): void {
    this.modal.set({ kind, parentId, projectId });
  }

  openCreateSchedule(projectId: number): void {
    const title = prompt('计划名称：');
    if (!title?.trim()) return;
    const type = prompt('类型（cron/once，默认 cron）：') || 'cron';
    const cron = type === 'cron' ? prompt('Cron 表达式（5字段，如 */5 * * * *）：') : '';
    if (type === 'cron' && !cron?.trim()) {
      this.notify('Cron 类型需要 cron 表达式', 'error');
      return;
    }
    void this.createNewSchedule(projectId, title.trim(), type, cron || '');
  }

  closeCreate(): void {
    this.modal.set(null);
  }

  modalTitle(): string {
    return (
      { project: '新建项目', epic: '新建 Epic', story: '新建 Story', task: '新建工作项' } as const
    )[this.modal()?.kind || 'project'];
  }

  async create(event: Event): Promise<void> {
    event.preventDefault();
    const modal = this.modal();
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const title = String(data.get('title') || '');
    const key = String(data.get('key') || '');
    const description = String(data.get('description') || '');
    const type = String(data.get('type') || 'task') as ItemType;
    const priority = String(data.get('priority') || 'medium') as Priority;
    if (!modal || !title.trim()) return;
    this.submitting.set(true);
    try {
      if (modal.kind === 'project') {
        await firstValueFrom(
          this.api.createProject({ name: title.trim(), key: key.trim() || undefined, description }),
        );
      } else if (modal.kind === 'epic' && modal.parentId) {
        await firstValueFrom(
          this.api.createEpic(modal.parentId, { title: title.trim(), description }),
        );
      } else if (modal.kind === 'story' && modal.parentId) {
        await firstValueFrom(
          this.api.createStory(modal.parentId, { title: title.trim(), description }),
        );
      } else if (modal.kind === 'task' && modal.parentId && modal.projectId) {
        await firstValueFrom(
          this.api.createTask(modal.parentId, {
            project_id: modal.projectId,
            title: title.trim(),
            description,
            type,
            priority,
          }),
        );
      }
      this.modal.set(null);
      this.notify('创建成功');
      await this.refresh();
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async saveProject(name: string, key: string, description: string): Promise<void> {
    const project = this.project();
    if (!project) return;
    await this.run('项目已更新', () =>
      firstValueFrom(this.api.updateProject(project.id, { name, key: key || null, description })),
    );
  }

  async saveEpic(title: string, description: string, status: Status): Promise<void> {
    const epic = this.epic();
    if (!epic) return;
    await this.run('Epic 已更新', () =>
      firstValueFrom(this.api.updateEpic(epic.id, { title, description, status })),
    );
  }

  async saveStory(title: string, description: string, status: Status): Promise<void> {
    const story = this.story();
    if (!story) return;
    await this.run('Story 已更新', () =>
      firstValueFrom(this.api.updateStory(story.id, { title, description, status })),
    );
  }

  async saveTask(
    title: string,
    description: string,
    spec: string,
    type: ItemType,
    priority: Priority,
  ): Promise<void> {
    const task = this.task();
    if (!task) return;
    await this.run('任务已保存', () =>
      firstValueFrom(this.api.updateTask(task.id, { title, description, spec, type, priority })),
    );
  }

  async changeTaskStatus(status: Status): Promise<void> {
    const task = this.task();
    if (!task || task.status === status) return;
    await this.run('状态已更新', () => firstValueFrom(this.api.setTaskStatus(task.id, status)));
  }

  async addComment(event: Event, author: string, content: string): Promise<void> {
    event.preventDefault();
    const task = this.task();
    if (!task || !author.trim() || !content.trim()) return;
    localStorage.setItem('agentboard_comment_author', author.trim());
    await this.run('评论已发布', () =>
      firstValueFrom(
        this.api.addComment(task.id, { author: author.trim(), content: content.trim() }),
      ),
    );
  }

  commentAuthor(): string {
    return localStorage.getItem('agentboard_comment_author') || this.currentUser() || '我';
  }

  // Task 603: 复制文本到剪贴板
  copyToClipboard(text: string): void {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => this.notify('已复制到剪贴板'));
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      this.notify('已复制到剪贴板');
    }
  }

  async remove(kind: 'project' | 'epic' | 'story' | 'task', id: number): Promise<void> {
    if (!confirm('确认删除？此操作不可撤销。')) return;
    try {
      if (kind === 'project') await firstValueFrom(this.api.deleteProject(id));
      if (kind === 'epic') await firstValueFrom(this.api.deleteEpic(id));
      if (kind === 'story') await firstValueFrom(this.api.deleteStory(id));
      if (kind === 'task') await firstValueFrom(this.api.deleteTask(id));
      this.notify('已删除');
      await this.router.navigateByUrl(
        kind === 'project'
          ? '/projects'
          : kind === 'epic'
            ? `/project/${this.project()?.id}`
            : kind === 'story'
              ? `/epic/${this.epic()?.id}`
              : `/story/${this.story()?.id}`,
      );
    } catch (error) {
      this.notify(`删除失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Sprint ---------- */

  async createSprint(event: Event, name: string, goal: string): Promise<void> {
    event.preventDefault();
    const project = this.project();
    if (!project || !name.trim()) return;
    this.submitting.set(true);
    try {
      await firstValueFrom(this.api.createSprint(project.id, { title: name.trim(), goal }));
      this.notify('Sprint 已创建');
      await this.loadSprints(project.id);
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async loadSprints(projectId: number): Promise<void> {
    this.sprints.set(await firstValueFrom(this.api.listSprints(projectId)));
  }

  async loadBacklog(projectId: number): Promise<void> {
    try {
      const tasks = await firstValueFrom(
        this.api.searchTasks({ project_id: projectId, limit: 200 }),
      );
      this.backlogTasks.set(tasks.filter((t) => !t.sprint_id));
    } catch {
      this.backlogTasks.set([]);
    }
  }

  readonly backlogVisibleTasks = computed(() => {
    const query = this.search().trim().toLocaleLowerCase();
    return query
      ? this.backlogTasks().filter((t) =>
          `${t.title} ${t.description} ${t.spec}`.toLocaleLowerCase().includes(query),
        )
      : this.backlogTasks();
  });

  async activateSprint(id: number): Promise<void> {
    if (!confirm('激活此 Sprint？同一项目只能有一个 active Sprint。')) return;
    try {
      await firstValueFrom(this.api.activateSprint(id));
      this.notify('Sprint 已激活');
      await this.refresh();
    } catch (error) {
      this.notify(`激活失败：${this.message(error)}`, 'error');
    }
  }

  async completeSprint(id: number): Promise<void> {
    if (!confirm('完成此 Sprint？未完成的任务将退回 Backlog。')) return;
    try {
      await firstValueFrom(this.api.completeSprint(id));
      this.notify('Sprint 已完成，未完成任务已退回 Backlog');
      await this.refresh();
    } catch (error) {
      this.notify(`完成失败：${this.message(error)}`, 'error');
    }
  }

  async deleteSprint(id: number): Promise<void> {
    if (!confirm('删除此 Sprint？')) return;
    try {
      await firstValueFrom(this.api.deleteSprint(id));
      this.notify('Sprint 已删除');
      await this.refresh();
    } catch (error) {
      this.notify(`删除失败：${this.message(error)}`, 'error');
    }
  }

  async deleteSchedule(id: number): Promise<void> {
    if (!confirm('删除此定时计划？')) return;
    const project = this.project();
    try {
      await firstValueFrom(this.api.deleteSchedule(id));
      this.notify('计划已删除');
      if (project) await this.loadSchedules(project.id);
    } catch (error) {
      this.notify(`删除失败：${this.message(error)}`, 'error');
    }
  }

  async assignTaskToSprint(taskId: number, sprintId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.updateTask(taskId, { sprint_id: sprintId } as Partial<Task>));
      this.notify('任务已加入 Sprint');
      await this.refresh();
    } catch (error) {
      this.notify(`分配失败：${this.message(error)}`, 'error');
    }
  }

  async removeTaskFromSprint(taskId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.updateTask(taskId, { sprint_id: null } as Partial<Task>));
      this.notify('任务已移出 Sprint');
      await this.refresh();
    } catch (error) {
      this.notify(`移除失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Bulk Operations ---------- */
  // Epic 21 Story 21.3: Shift+点击多选支持
  toggleTaskSelection(taskId: number, event?: Event): void {
    const selected = new Set(this.selectedTasks());
    const mouseEvent = event as MouseEvent | undefined;
    
    // Shift+点击：范围选择
    if (mouseEvent?.shiftKey && this.lastSelectedTaskId() !== null) {
      const tasks = this.visibleTasks();
      const lastIdx = tasks.findIndex(t => t.id === this.lastSelectedTaskId());
      const currentIdx = tasks.findIndex(t => t.id === taskId);
      if (lastIdx >= 0 && currentIdx >= 0) {
        const [start, end] = lastIdx < currentIdx ? [lastIdx, currentIdx] : [currentIdx, lastIdx];
        for (let i = start; i <= end; i++) {
          selected.add(tasks[i].id);
        }
      }
    } else {
      // 普通点击切换
      if (selected.has(taskId)) {
        selected.delete(taskId);
      } else {
        selected.add(taskId);
      }
    }
    
    this.selectedTasks.set(selected);
    this.lastSelectedTaskId.set(taskId);
  }

  selectAllTasks(): void {
    const allIds = new Set(this.visibleTasks().map(t => t.id));
    this.selectedTasks.set(allIds);
  }

  clearTaskSelection(): void {
    this.selectedTasks.set(new Set());
    this.lastSelectedTaskId.set(null);
  }

  get selectedTaskCount(): number {
    return this.selectedTasks().size;
  }

  // Epic 21 Story 21.3: 批量操作进度跟踪
  async bulkUpdateStatus(newStatus: string): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;
    
    // 显示进度
    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在更新 0/${ids.length} 个任务…` });
    
    try {
      const result = await firstValueFrom(this.api.bulkUpdateTasks(ids, { status: newStatus }));
      const successCount = result.updated?.length ?? 0;
      const errorCount = result.errors?.length ?? 0;
      
      // Story 21.3: 失败反馈优化 - 显示具体失败项
      if (errorCount > 0) {
        const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
        const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
        this.notify(`批量更新完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
      } else {
        this.notify(`已批量更新 ${successCount} 个任务的状态为「${this.statusLabel(newStatus)}」`);
      }
      this.clearTaskSelection();
      await this.refresh();
    } catch (error) {
      // Epic 21 Story 21.4: 离线队列重放时的错误处理
      const errorMsg = error instanceof Error ? error.message : String(error);
      if (errorMsg.includes('离线')) {
        this.notify('操作已加入离线队列，将在网络恢复后自动重试', 'error');
      } else {
        this.notify(`批量更新失败：${errorMsg}`, 'error');
      }
    } finally {
      this.bulkProgress.set(null);
    }
  }

  async bulkDeleteTasks(): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;
    if (!confirm(`确认删除选中的 ${ids.length} 个任务？此操作不可撤销。`)) return;
    
    // 显示进度
    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在删除 0/${ids.length} 个任务…` });
    
    try {
      const result = await firstValueFrom(this.api.bulkDeleteTasks(ids));
      const successCount = result.deleted?.length ?? 0;
      const errorCount = result.errors?.length ?? 0;
      
      // Story 21.3: 失败反馈优化 - 显示具体失败项
      if (errorCount > 0) {
        const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
        const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
        this.notify(`批量删除完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
      } else {
        this.notify(`已删除 ${successCount} 个任务`);
      }
      this.clearTaskSelection();
      await this.refresh();
    } catch (error) {
      // Epic 21 Story 21.4: 离线队列重放时的错误处理
      const errorMsg = error instanceof Error ? error.message : String(error);
      if (errorMsg.includes('离线')) {
        this.notify('操作已加入离线队列，将在网络恢复后自动重试', 'error');
      } else {
        this.notify(`批量删除失败：${errorMsg}`, 'error');
      }
    } finally {
      this.bulkProgress.set(null);
    }
  }

  // Task 711: 批量删除 - 快捷键触发
  bulkDelete(): void {
    if (this.selectedTasks().size > 0) {
      void this.bulkDeleteTasks();
    }
  }

  showBulkActionPanel(type: 'status' | 'delete'): void {
    this.bulkActionTarget.set(type);
  }

  closeBulkActionPanel(): void {
    this.bulkActionTarget.set(null);
  }

  /* ---------- Keyboard Navigation ---------- */
  handleTaskKeydown(event: KeyboardEvent): void {
    // Skip if in input/textarea/select
    const target = event.target as HTMLElement;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') {
      return;
    }
    const tasks = this.visibleTasks();
    if (tasks.length === 0) return;
    const idx = this.focusedTaskIndex();
    switch (event.key) {
      case 'j':
      case 'ArrowDown':
        event.preventDefault();
        if (idx < tasks.length - 1) {
          this.focusedTaskIndex.set(idx + 1);
          this.scrollToFocusedTask();
        }
        break;
      case 'k':
      case 'ArrowUp':
        event.preventDefault();
        if (idx > 0) {
          this.focusedTaskIndex.set(idx - 1);
          this.scrollToFocusedTask();
        } else if (idx === -1 && tasks.length > 0) {
          this.focusedTaskIndex.set(0);
          this.scrollToFocusedTask();
        }
        break;
      case 'Enter':
        if (idx >= 0 && idx < tasks.length) {
          event.preventDefault();
          this.router.navigate(['/task', tasks[idx].id]);
        }
        break;
      case ' ':
        if (idx >= 0 && idx < tasks.length) {
          event.preventDefault();
          this.toggleTaskSelection(tasks[idx].id);
        }
        break;
      case 'Escape':
        this.focusedTaskIndex.set(-1);
        this.clearTaskSelection();
        this.closeBulkActionPanel();
        break;
      case 'a':
        // Ctrl+A / Cmd+A: Select all tasks
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault();
          this.selectAllTasks();
        }
        break;
    }
  }

  private scrollToFocusedTask(): void {
    setTimeout(() => {
      const el = document.querySelector('.entity-item.kbd-focused');
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }, 0);
  }

  isTaskFocused(index: number): boolean {
    return this.focusedTaskIndex() === index;
  }

  async loadSprintBurndown(sprintId: number): Promise<void> {
    try {
      const data = await firstValueFrom(this.api.getSprintBurndown(sprintId));
      this.sprintBurndown.set(data);
    } catch {
      this.sprintBurndown.set(null);
    }
  }

  sprintStatusLabel(status: string): string {
    return (
      (
        { planning: '规划中', active: '进行中', completed: '已完成' } as Record<string, string>
      )[status] || status
    );
  }

  /* ---------- Members ---------- */
  async loadMembers(projectId: number): Promise<void> {
    try {
      const result = await firstValueFrom(this.api.listMembers(projectId));
      this.members.set(result.items);
    } catch {
      this.members.set([]);
    }
  }

  async checkProjectOwner(projectId: number): Promise<void> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) { this.isOwner.set(false); this.isAdmin.set(false); return; }
    try {
      const me = await firstValueFrom(this.api.me());
      this.isAdmin.set(me.is_admin ?? false);
      // 从成员列表判断是否 owner
      const result = await firstValueFrom(this.api.listMembers(projectId));
      const myMember = result.items.find((m: ProjectMember) => m.user_id === me.id);
      this.isOwner.set(myMember?.role === 'owner');
    } catch {
      this.isOwner.set(false); this.isAdmin.set(false);
    }
  }

  async addMember(projectId: number, username: string, role: string = 'member'): Promise<void> {
    try {
      await firstValueFrom(this.api.addMember(projectId, { username, role }));
      this.notify('成员已添加');
      await this.loadMembers(projectId);
    } catch (error) {
      this.notify(`添加失败：${this.message(error)}`, 'error');
    }
  }

  async removeMember(projectId: number, userId: number): Promise<void> {
    if (!confirm('确认移除该成员？')) return;
    try {
      await firstValueFrom(this.api.removeMember(projectId, userId));
      this.notify('成员已移除');
      await this.loadMembers(projectId);
    } catch (error) {
      this.notify(`移除失败：${this.message(error)}`, 'error');
    }
  }

  async updateMemberRole(projectId: number, userId: number, role: string): Promise<void> {
    try {
      await firstValueFrom(this.api.updateMemberRole(projectId, userId, role));
      this.notify('角色已更新');
      await this.loadMembers(projectId);
    } catch (error) {
      this.notify(`更新失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Notifications ---------- */
  async loadNotifications(): Promise<void> {
    try {
      const [notifs, count] = await Promise.all([
        firstValueFrom(this.api.listNotifications({ limit: 20 })),
        firstValueFrom(this.api.getUnreadCount()),
      ]);
      this.notifications.set(notifs.items);
      this.unreadCount.set(count.count);
    } catch {
      this.notifications.set([]);
      this.unreadCount.set(0);
    }
  }

  async markRead(notifId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.markRead(notifId));
      await this.loadNotifications();
    } catch { /* ignore */ }
  }

  async markAllRead(): Promise<void> {
    try {
      await firstValueFrom(this.api.markAllRead());
      await this.loadNotifications();
    } catch { /* ignore */ }
  }

  async deleteNotification(notifId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.deleteNotification(notifId));
      await this.loadNotifications();
    } catch { /* ignore */ }
  }

  toggleNotifications(): void {
    const current = this.showNotifications();
    this.showNotifications.set(!current);
    if (!current) { void this.loadNotifications(); }
  }

  /* ---------- Project Stats ---------- */
  async loadProjectStats(projectId: number): Promise<void> {
    try {
      const stats = await firstValueFrom(this.api.getProjectStats(projectId));
      this.projectStats.set(stats);
    } catch {
      this.projectStats.set(null);
    }
  }

  /* ---------- Attachment ---------- */
  async loadAttachments(taskId: number): Promise<void> {
    try {
      this.attachments.set(await firstValueFrom(this.api.listAttachments(taskId)));
    } catch {
      this.attachments.set([]);
    }
  }

  async onAttachmentFileSelected(event: Event, taskId: number): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    try {
      await firstValueFrom(this.api.uploadAttachment(taskId, file));
      this.notify('附件已上传');
      await this.loadAttachments(taskId);
    } catch (error) {
      this.notify(`上传失败：${this.message(error)}`, 'error');
    }
    input.value = '';
  }

  async deleteAttachment(taskId: number, attachmentId: number): Promise<void> {
    if (!confirm('确认删除此附件？')) return;
    try {
      await firstValueFrom(this.api.deleteAttachment(attachmentId));
      this.notify('附件已删除');
      await this.loadAttachments(taskId);
    } catch (error) {
      this.notify(`删除失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Schedules ---------- */
  async loadSchedules(projectId: number): Promise<void> {
    try {
      this.schedules.set(await firstValueFrom(this.api.listSchedules(projectId)));
    } catch {
      this.schedules.set([]);
    }
  }

  async toggleSchedule(scheduleId: number, enabled: boolean): Promise<void> {
    try {
      await firstValueFrom(this.api.updateSchedule(scheduleId, { enabled } as Partial<AgentSchedule>));
      this.notify(enabled ? '计划已启用' : '计划已停用');
      const project = this.project();
      if (project) await this.loadSchedules(project.id);
    } catch (error) {
      this.notify(`操作失败：${this.message(error)}`, 'error');
    }
  }

  async createNewSchedule(projectId: number, title: string, scheduleType: string, cronExpr: string): Promise<void> {
    try {
      await firstValueFrom(this.api.createSchedule(projectId, {
        title,
        schedule_type: scheduleType as 'cron' | 'once',
        cron_expr: cronExpr || undefined,
      }));
      this.notify('计划已创建');
      await this.loadSchedules(projectId);
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    }
  }

  async saveProjectSettings(name: string, key: string, description: string, isPrivate: boolean): Promise<void> {
    const project = this.project();
    if (!project) return;
    try {
      await firstValueFrom(this.api.updateProject(project.id, { name, key: key || null, description, is_private: isPrivate }));
      this.notify('项目设置已保存');
      await this.loadRoute();
    } catch (error) {
      this.notify(`保存失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Admin ---------- */
  async adminMe(): Promise<{ id: number; username: string; is_admin: boolean } | null> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) return null;
    try {
      return await firstValueFrom(this.api.me());
    } catch {
      return null;
    }
  }

  async loadAdminData(): Promise<void> {
    try {
      const [usersResult, projectsResult] = await Promise.all([
        firstValueFrom(this.api.adminListUsers({ limit: 100 })),
        firstValueFrom(this.api.adminListProjects({ limit: 100 })),
      ]);
      this.adminUsers.set(usersResult.items);
      this.adminProjects.set(projectsResult.items);
    } catch {
      this.adminUsers.set([]);
      this.adminProjects.set([]);
    }
  }

  async setAdmin(userId: number, isAdmin: boolean): Promise<void> {
    try {
      await firstValueFrom(this.api.adminUpdateUser(userId, isAdmin));
      this.notify(isAdmin ? '已设为管理员' : '已撤销管理员权限');
      await this.loadAdminData();
    } catch (error) {
      this.notify(`操作失败：${this.message(error)}`, 'error');
    }
  }

  async adminDeleteProject(projectId: number): Promise<void> {
    if (!confirm('确认删除此项目？此操作不可撤销！')) return;
    try {
      await firstValueFrom(this.api.adminDeleteProject(projectId));
      this.notify('项目已删除');
      await this.loadAdminData();
    } catch (error) {
      this.notify(`删除失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Epic 25: API Keys ---------- */
  async loadApiKeys(): Promise<void> {
    try {
      const resp = await firstValueFrom(this.api.listApiKeys());
      this.apiKeys.set(resp.items || []);
    } catch {
      this.apiKeys.set([]);
    }
  }

  showCreateKeyModal(): void {
    this.newKeyName.set('');
    this.newKeyPerms.set('');
    this.createdKeyPlaintext.set('');
    this.keyModalVisible.set(true);
  }

  closeKeyModal(): void {
    this.keyModalVisible.set(false);
    this.createdKeyPlaintext.set('');
  }

  async createApiKey(): Promise<void> {
    const name = this.newKeyName().trim();
    if (!name) { this.notify('请输入密钥名称', 'error'); return; }
    const perms = this.newKeyPerms().trim()
      ? this.newKeyPerms().trim().split(',').map(p => p.trim()).filter(Boolean)
      : ['api:read'];
    try {
      const result = await firstValueFrom(this.api.createApiKey({ name, permissions: perms }));
      this.createdKeyPlaintext.set(result.key);
      this.notify('API Key 已创建，请立即复制保存！');
      await this.loadApiKeys();
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    }
  }

  async revokeApiKey(keyId: number): Promise<void> {
    if (!confirm('确认撤销此 API Key？撤销后使用该 Key 的请求将立即失效。')) return;
    try {
      await firstValueFrom(this.api.revokeApiKey(keyId));
      this.notify('API Key 已撤销');
      await this.loadApiKeys();
    } catch (error) {
      this.notify(`撤销失败：${this.message(error)}`, 'error');
    }
  }

  async generate(): Promise<void> {
    const task = this.task();
    if (!task) return;
    await this.run('子任务生成完成', () => firstValueFrom(this.api.generateSubtasks(task.id)));
  }

  toggleTheme(): void {
    this.applyTheme(this.document.documentElement.dataset['theme'] === 'dark' ? 'light' : 'dark');
  }

  getThemeLabel(): string {
    const isDark = this.isDarkTheme();
    const isSystem = !localStorage.getItem('agentboard_theme');
    const base = isDark ? '切换到浅色模式' : '切换到深色模式';
    return isSystem ? `${base}（跟随系统）` : base;
  }

  isDarkTheme(): boolean {
    return this.document.documentElement.dataset['theme'] === 'dark';
  }

  setBoardMode(board: boolean): void {
    this.boardMode.set(board);
    localStorage.setItem('agentboard_story_view', board ? 'board' : 'list');
  }

  tasksForStatus(status: Status): Task[] {
    // Task 714/715: 虚拟滚动 + 增量渲染优化
    // memoize: Angular signals auto-caches based on dependencies
    const all = this.visibleTasks().filter((task) => task.status === status);
    return all.slice(0, this.taskPageSize() * this.taskPageCount());
  }

  private applyTheme(theme: string): void {
    this.document.documentElement.dataset['theme'] = theme;
    localStorage.setItem('agentboard_theme', theme);
  }

  statusLabel(status: string): string {
    return (
      (
        {
          backlog: '待规划',
          todo: '待办',
          in_progress: '进行中',
          in_review: '评审中',
          verifying: '验证中',
          done: '完成',
        } as Record<string, string>
      )[status] || status
    );
  }

  priorityLabel(priority: string): string {
    return (
      (
        { highest: '最高', high: '高', medium: '中', low: '低', lowest: '最低' } as Record<
          string,
          string
        >
      )[priority] || priority
    );
  }

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

  private async run(success: string, action: () => Promise<unknown>): Promise<void> {
    try {
      await action();
      this.notify(success);
      await this.refresh();
    } catch (error) {
      this.notify(`${success.replace(/已|完成/g, '')}失败：${this.message(error)}`, 'error');
    }
  }

  private notify(message: string, type: 'success' | 'error' = 'success'): void {
    // Epic 24 Story 24.2: Toast 多行+可关闭+最大数量限制
    const id = ++this._toastCounter;
    const MAX_TOASTS = 3;
    const current = this.toasts();
    const updated: { id: number; message: string; type: 'success' | 'error' }[] = [{ id, message, type }, ...current].slice(0, MAX_TOASTS);
    this.toasts.set(updated);
    setTimeout(() => this.closeToast(id), 5000);
    // 保持旧信号兼容
    this.toastMessage.set(message);
    this.toastType.set(type);
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.toastMessage.set(''); }, 4000);
  }

  closeToast(id: number): void {
    // Epic 24 Story 24.2: 关闭指定 toast
    this.toasts.set(this.toasts().filter((t: { id: number }) => t.id !== id));
  }

  private message(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  /* ---------- Epic 22: Task Dependencies ---------- */
  loadTaskDependencies(taskId: number): void {
    void this.loadTaskDependenciesAsync(taskId);
  }

  private async loadTaskDependenciesAsync(taskId: number): Promise<void> {
    try {
      const deps = await firstValueFrom(this.api.getTaskDependencies(taskId));
      this.taskDependencies.set(deps);
    } catch (error) {
      this.notify(`加载依赖失败：${this.message(error)}`, 'error');
    }
  }

  addDependency(taskId: number, dependsOnId: number, type: string = 'blocks'): void {
    void this.addDependencyAsync(taskId, dependsOnId, type);
  }

  private async addDependencyAsync(taskId: number, dependsOnId: number, type: string): Promise<void> {
    try {
      await firstValueFrom(this.api.addTaskDependency(taskId, dependsOnId, type));
      await this.loadTaskDependenciesAsync(taskId);
      this.notify('依赖添加成功');
    } catch (error) {
      this.notify(`添加依赖失败：${this.message(error)}`, 'error');
    }
  }

  removeDependency(dependencyId: number, taskId: number): void {
    void this.removeDependencyAsync(dependencyId, taskId);
  }

  private async removeDependencyAsync(dependencyId: number, taskId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.removeTaskDependency(dependencyId));
      await this.loadTaskDependenciesAsync(taskId);
      this.notify('依赖已移除');
    } catch (error) {
      this.notify(`移除依赖失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Epic 22: Webhooks ---------- */
  loadWebhooks(projectId?: number): void {
    void this.loadWebhooksAsync(projectId);
  }

  private async loadWebhooksAsync(projectId?: number): Promise<void> {
    try {
      const resp = await firstValueFrom(this.api.listWebhooks(projectId));
      this.webhooks.set(resp.items || []);
    } catch (error) {
      this.notify(`加载 Webhooks 失败：${this.message(error)}`, 'error');
    }
  }

  createWebhook(name: string, url: string, secret: string, events: string[], projectId?: number): void {
    void this.createWebhookAsync(name, url, secret, events, projectId);
  }

  private async createWebhookAsync(name: string, url: string, secret: string, events: string[], projectId?: number): Promise<void> {
    try {
      await firstValueFrom(this.api.createWebhook(projectId, { name, url, secret: secret || undefined, events }));
      await this.loadWebhooksAsync(projectId);
      this.notify('Webhook 创建成功');
    } catch (error) {
      this.notify(`创建 Webhook 失败：${this.message(error)}`, 'error');
    }
  }

  deleteWebhook(webhookId: number, projectId?: number): void {
    void this.deleteWebhookAsync(webhookId, projectId);
  }

  private async deleteWebhookAsync(webhookId: number, projectId?: number): Promise<void> {
    try {
      await firstValueFrom(this.api.deleteWebhook(webhookId));
      await this.loadWebhooksAsync(projectId);
      this.notify('Webhook 已删除');
    } catch (error) {
      this.notify(`删除 Webhook 失败：${this.message(error)}`, 'error');
    }
  }

  toggleWebhook(webhookId: number, enabled: boolean, projectId?: number): void {
    void this.toggleWebhookAsync(webhookId, enabled, projectId);
  }

  private async toggleWebhookAsync(webhookId: number, enabled: boolean, projectId?: number): Promise<void> {
    try {
      await firstValueFrom(this.api.toggleWebhook(webhookId, enabled));
      await this.loadWebhooksAsync(projectId);
    } catch (error) {
      this.notify(`更新 Webhook 失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Epic 22: Audit Logs ---------- */
  loadAuditLogs(params?: { entity_type?: string; entity_id?: number }): void {
    void this.loadAuditLogsAsync(params);
  }

  private async loadAuditLogsAsync(params?: { entity_type?: string; entity_id?: number }): Promise<void> {
    try {
      const resp = await firstValueFrom(this.api.listAuditLogs({ ...params, limit: 50 }));
      this.auditLogs.set(resp.items || []);
    } catch (error) {
      this.notify(`加载审计日志失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Export ---------- */
  exportToCSV(tasks?: Task[]): void {
    const items = tasks || this.visibleTasks();
    if (items.length === 0) {
      this.notify('没有可导出的任务', 'error');
      return;
    }
    const headers = ['ID', '标题', '类型', '状态', '优先级', '描述', 'Spec', '创建时间', '更新时间'];
    const rows = items.map(t => [
      t.id,
      `"${(t.title || '').replace(/"/g, '""')}"`,
      t.type,
      t.status,
      t.priority,
      `"${(t.description || '').replace(/"/g, '""')}"`,
      `"${(t.spec || '').replace(/"/g, '""')}"`,
      t.created_at,
      t.updated_at,
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    this.downloadFile(csv, `tasks-${Date.now()}.csv`, 'text/csv;charset=utf-8');
    this.notify(`已导出 ${items.length} 个任务到 CSV`);
  }

  /* ---------- Epic 22: Import/Export Handlers ---------- */
  onImportFileSelected(event: Event, projectId: number): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target?.result as string);
        void this.importTasksAsync(projectId, data);
      } catch {
        this.notify('JSON 解析失败，请检查文件格式', 'error');
      }
    };
    reader.readAsText(file);
  }

  private async importTasksAsync(projectId: number, data: any): Promise<void> {
    try {
      const result = await firstValueFrom(this.api.importTasks(projectId, data.tasks || []));
      const resultEl = document.getElementById('import-result');
      if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.textContent = `导入完成：成功 ${result.imported.length} 个，失败 ${result.errors.length} 个`;
        resultEl.className = result.errors.length > 0 ? 'info-box warning' : 'info-box';
      }
      this.notify(`导入完成：成功 ${result.imported.length} 个`);
    } catch (error) {
      this.notify(`导入失败：${this.message(error)}`, 'error');
    }
  }

  onCreateWebhook(projectId: number): void {
    const nameEl = document.getElementById('wh-name') as HTMLInputElement;
    const urlEl = document.getElementById('wh-url') as HTMLInputElement;
    const secretEl = document.getElementById('wh-secret') as HTMLInputElement;
    const name = nameEl?.value?.trim();
    const url = urlEl?.value?.trim();
    const secret = secretEl?.value?.trim();
    if (!name || !url) {
      this.notify('名称和 URL 不能为空', 'error');
      return;
    }
    this.createWebhook(name, url, secret, [], projectId);
    if (nameEl) nameEl.value = '';
    if (urlEl) urlEl.value = '';
    if (secretEl) secretEl.value = '';
  }

  exportToJSON(tasks?: Task[]): void {
    const items = tasks || this.visibleTasks();
    if (items.length === 0) {
      this.notify('没有可导出的任务', 'error');
      return;
    }
    const json = JSON.stringify(items, null, 2);
    this.downloadFile(json, `tasks-${Date.now()}.json`, 'application/json');
    this.notify(`已导出 ${items.length} 个任务到 JSON`);
  }

  exportProjectTasks(): void {
    const project = this.project();
    if (!project) {
      this.notify('请先选择一个项目', 'error');
      return;
    }
    void this.exportProjectTasksAsync(project.id);
  }

  private async exportProjectTasksAsync(projectId: number): Promise<void> {
    try {
      // Fetch all epics, stories, and tasks for the project
      const epics = await firstValueFrom(this.api.listEpics(projectId));
      const allTasks: Task[] = [];
      for (const epic of epics) {
        const stories = await firstValueFrom(this.api.listStories(epic.id));
        for (const story of stories) {
          const tasks = await firstValueFrom(this.api.listTasks(story.id));
          allTasks.push(...tasks);
        }
      }
      // Export all tasks
      this.exportToCSV(allTasks);
    } catch (error) {
      this.notify(`导出失败：${this.message(error)}`, 'error');
    }
  }

  private downloadFile(content: string, filename: string, mimeType: string): void {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  // Task 714: 虚拟滚动优化 - 分页加载更多
  loadMoreTasks(): void {
    this.taskPageCount.update((n) => n + 1);
  }

  // Task 716: 全局快捷键面板 - 切换显示
  toggleShortcuts(): void {
    this.showShortcuts.set(!this.showShortcuts());
  }

  // Task 716: 全局快捷键面板 - 快捷键说明
  // Task 710/711: 增强快捷键提示面板 + 批量选择键盘支持
  readonly shortcuts = [
    { group: '导航', items: [
      { keys: ['j'], desc: '下一项' },
      { keys: ['k'], desc: '上一项' },
      { keys: ['Enter'], desc: '打开选中项' },
      { keys: ['Esc'], desc: '关闭弹层' },
    ]},
    { group: '编辑', items: [
      { keys: ['e'], desc: '编辑选中项' },
      { keys: ['n'], desc: '新建项目' },
      { keys: ['Ctrl', '↵'], desc: '提交表单' },
    ]},
    { group: '批量操作', items: [
      { keys: ['Shift', '点击'], desc: '范围多选' },
      { keys: ['Ctrl', 'A'], desc: '全选当前列表' },
      { keys: ['Ctrl', '点击'], desc: '单项切换选择' },
      { keys: ['Del'], desc: '删除选中项' },
    ]},
    { group: '视图', items: [
      { keys: ['v'], desc: '切换列表/看板' },
      { keys: ['s'], desc: '搜索' },
      { keys: ['/'], desc: '聚焦搜索框' },
    ]},
    { group: '系统', items: [
      { keys: ['?'], desc: '显示快捷键面板' },
      { keys: ['t'], desc: '切换主题' },
      { keys: ['b'], desc: '切换侧栏' },
    ]},
  ];
}
