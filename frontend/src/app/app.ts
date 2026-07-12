import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';

import { ApiService } from './api.service';
import { Comment, Epic, ItemType, Priority, Project, Status, Story, Task } from './models';

type ViewKind = 'home' | 'projects' | 'project' | 'epic' | 'story' | 'task' | 'not-found';
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
})
export class App implements OnInit, OnDestroy {
  readonly projects = signal<Project[]>([]);
  readonly epics = signal<Epic[]>([]);
  readonly stories = signal<Story[]>([]);
  readonly tasks = signal<Task[]>([]);
  readonly comments = signal<Comment[]>([]);
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
  ) {}

  ngOnInit(): void {
    this.applyTheme(localStorage.getItem('agentboard_theme') || 'light');
    this.routeSub = this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.loadRoute());
    void this.loadRoute();
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
        const [project, epics] = await Promise.all([
          firstValueFrom(this.api.getProject(id)),
          firstValueFrom(this.api.listEpics(id)),
        ]);
        this.project.set(project);
        this.epics.set(epics);
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
        if (task.story_id) {
          const story = await firstValueFrom(this.api.getStory(task.story_id));
          this.story.set(story);
          const epic = await firstValueFrom(this.api.getEpic(story.epic_id));
          this.epic.set(epic);
          this.project.set(await firstValueFrom(this.api.getProject(epic.project_id)));
        }
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
    this.projects.set(await firstValueFrom(this.api.listProjects()));
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
      this.currentUser.set(result.username);
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
    this.currentUser.set('');
    this.openAuth('login');
    this.notify('已退出登录');
  }

  openCreate(kind: CreateKind, parentId?: number, projectId?: number): void {
    this.modal.set({ kind, parentId, projectId });
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

  async generate(): Promise<void> {
    const task = this.task();
    if (!task) return;
    await this.run('子任务生成完成', () => firstValueFrom(this.api.generateSubtasks(task.id)));
  }

  toggleTheme(): void {
    this.applyTheme(document.documentElement.dataset['theme'] === 'dark' ? 'light' : 'dark');
  }

  setBoardMode(board: boolean): void {
    this.boardMode.set(board);
    localStorage.setItem('agentboard_story_view', board ? 'board' : 'list');
  }

  tasksForStatus(status: Status): Task[] {
    return this.visibleTasks().filter((task) => task.status === status);
  }

  private applyTheme(theme: string): void {
    document.documentElement.dataset['theme'] = theme;
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
