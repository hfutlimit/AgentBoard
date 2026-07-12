import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, ViewEncapsulation, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';
import { DOCUMENT } from '@angular/common';
import { Inject } from '@angular/core';
import { filter } from 'rxjs/operators';

import { ApiService } from './api.service';
import { AgentSchedule, Attachment, Comment, Epic, ItemType, Notification, Priority, Project, ProjectMember, ProjectStats, Sprint, SprintStatus, Status, Story, Task } from './models';

type ViewKind = 'home' | 'projects' | 'project' | 'epic' | 'story' | 'task' | 'sprint' | 'admin' | 'not-found';
type CreateKind = 'project' | 'epic' | 'story' | 'task';

interface CreateModal {
  kind: CreateKind;
  parentId?: number;
  projectId?: number;
}

@Component({
  selector: 'app-root',
  imports: [CommonModule, FormsModule, RouterLink, RouterOutlet],
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
  readonly authVisible = signal(false);
  readonly authMode = signal<'login' | 'register'>('login');
  readonly currentUser = signal(localStorage.getItem('agentboard_user') || '');
  readonly toastMessage = signal('');
  readonly toastType = signal<'success' | 'error'>('success');
  readonly modal = signal<CreateModal | null>(null);
  readonly submitting = signal(false);
  readonly activeTab = signal<'epics' | 'sprints' | 'backlog' | 'settings' | 'members' | 'stats' | 'schedules'>('epics');
  readonly members = signal<ProjectMember[]>([]);
  readonly notifications = signal<Notification[]>([]);
  readonly unreadCount = signal(0);
  readonly showNotifications = signal(false);
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
  readonly attachments = signal<Attachment[]>([]);
  readonly adminUsers = signal<any[]>([]);
  readonly adminProjects = signal<any[]>([]);
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
  readonly visibleTasks = computed(() =>
    this.match(this.tasks(), (t) => `${t.title} ${t.description} ${t.spec}`),
  );
  readonly doneTasks = computed(() => this.tasks().filter((t) => t.status === 'done').length);

  private routeSub?: Subscription;
  private toastTimer?: ReturnType<typeof setTimeout>;

  constructor(
    readonly api: ApiService,
    private readonly router: Router,
    @Inject(DOCUMENT) private readonly document: Document,
  ) {}

  ngOnInit(): void {
    const saved = localStorage.getItem('agentboard_theme');
    // 优先使用用户偏好，其次跟随系统
    const theme = saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    this.applyTheme(theme);
    this.loadRecentProjects();
    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      if (!localStorage.getItem('agentboard_theme')) {
        this.applyTheme(e.matches ? 'dark' : 'light');
      }
    });
    // 启动时校验已有 token，失败则清除并显示登录
    void this.validateAuth();
    this.routeSub = this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.loadRoute());
    void this.loadRoute();
    void this.checkHealth();
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
    if (this.showHealth()) void this.checkHealth();
  }

  /** 启动时验证 localStorage 中的 token，有效则恢复登录态，无效则清除并弹登录 */
  private async validateAuth(): Promise<void> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) return; // 无 token，不弹登录（后端开放时免登可用）
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

  ngOnDestroy(): void {
    this.routeSub?.unsubscribe();
    if (this.toastTimer) clearTimeout(this.toastTimer);
  }

  private match<T>(items: T[], text: (item: T) => string): T[] {
    const query = this.search().trim().toLocaleLowerCase();
    return query ? items.filter((item) => text(item).toLocaleLowerCase().includes(query)) : items;
  }

  private async loadRoute(): Promise<void> {
    this.loading.set(true);
    this.error.set('');
    const path = this.router.url.split('?')[0].replace(/^\//, '');
    const [kind = '', rawId] = path.split('/');
    const id = Number(rawId);
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
    this.authMode.set(mode);
    this.authVisible.set(true);
  }

  closeAuth(): void {
    this.authVisible.set(false);
  }

  async authenticate(event: Event, username: string, password: string): Promise<void> {
    event.preventDefault();
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
      await this.loadRoute();
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
    this.openAuth('login');
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
    return this.visibleTasks().filter((task) => task.status === status);
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
    this.toastMessage.set(message);
    this.toastType.set(type);
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => this.toastMessage.set(''), 4000);
  }

  private message(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }
}
