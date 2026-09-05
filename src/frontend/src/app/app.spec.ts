import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, Subject } from 'rxjs';
import { delay, tap } from 'rxjs/operators';
import { vi } from 'vitest';

import { ApiService } from './api.service';
import { App } from './app';
import { routes } from './app.routes';
import { DocumentItem, Epic, OverviewStats, Project, ProposalItem, Sprint, Story, Task } from './models';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter(routes),
        {
          provide: ApiService,
          useValue: { baseUrl: 'http://test', listProjects: () => of([]) },
        },
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should derive Epic status filters from an immutable stable newest-first list and reset pagination', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    const epics = [
      { id: 101, project_id: 1, title: 'Old todo', description: '', status: 'todo', created_at: '2026-09-01T00:00:00Z' },
      { id: 104, project_id: 1, title: 'Same timestamp todo', description: '', status: 'todo', created_at: '2026-09-03T00:00:00Z' },
      { id: 103, project_id: 1, title: 'Same timestamp progress', description: '', status: 'in_progress', created_at: '2026-09-03T00:00:00Z' },
      { id: 102, project_id: 1, title: 'Review', description: '', status: 'in_review', created_at: '2026-09-02T00:00:00Z' },
      { id: 105, project_id: 1, title: 'Done', description: '', status: 'done', created_at: '2026-09-04T00:00:00Z' },
      { id: 106, project_id: 1, title: 'Blocked', description: '', status: 'blocked', created_at: '2026-09-05T00:00:00Z' },
      { id: 107, project_id: 1, title: 'Legacy backlog', description: '', status: 'backlog', created_at: '2026-09-06T00:00:00Z' },
      { id: 108, project_id: 1, title: 'Legacy verifying', description: '', status: 'verifying', created_at: '2026-09-07T00:00:00Z' },
    ] as unknown as Epic[];
    const originalIds = epics.map((epic) => epic.id);
    app.epics.set(epics);

    expect(app.epicFilterStatus()).toBe('');
    expect(app.visibleEpics().map((epic) => epic.id)).toEqual([108, 107, 106, 105, 104, 103, 102, 101]);
    expect(app.epics().map((epic) => epic.id)).toEqual(originalIds);

    for (const status of app.epicListFilterStatuses) {
      app.epicsPage.set(3);
      app.setEpicFilterStatus(status);
      expect(app.epicsPage()).toBe(1);
      expect(app.visibleEpics().every((epic) => epic.status === status)).toBe(true);
    }

    app.setEpicFilterStatus('todo');
    expect(app.visibleEpics().map((epic) => epic.id)).toEqual([104, 101]);
    app.epics.set(epics.filter((epic) => epic.status !== 'done'));
    app.setEpicFilterStatus('done');
    expect(app.visibleEpics()).toEqual([]);
    app.epics.set(epics);
    app.setEpicFilterStatus('');
    expect(app.visibleEpics().map((epic) => epic.id)).toEqual([108, 107, 106, 105, 104, 103, 102, 101]);
    app.setEpicFilterStatus('unexpected' as never);
    expect(app.epicFilterStatus()).toBe('');
  });

  it('should refresh every visible project collection after creating a project', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    const api = TestBed.inject(ApiService) as unknown as Record<string, ReturnType<typeof vi.fn>>;
    const created = {
      id: 42,
      name: 'Fresh Project',
      key: 'FP',
      description: '',
      is_private: false,
      created_at: '2026-08-20T00:00:00',
    } satisfies Project;

    api['createProject'] = vi.fn(() => of(created));
    api['listProjects'] = vi.fn(() => of([created]));
    api['listProjectsCenter'] = vi.fn(() => of({ items: [created], total: 1 }));
    vi.spyOn(app as any, 'loadDashboard').mockResolvedValue(undefined);

    const form = document.createElement('form');
    form.innerHTML = '<input name="title" value="Fresh Project"><input name="key" value="FP">';
    app.authVisible.set(false);
    app.projectsCenter.set([{ ...created, id: 7, name: 'Existing Project', key: 'OLD' }]);
    app.modal.set({ kind: 'project' });

    await app.create({ preventDefault: vi.fn(), currentTarget: form } as unknown as Event);

    expect(api['createProject']).toHaveBeenCalledWith({ name: 'Fresh Project', key: 'FP', description: '' });
    expect(api['listProjectsCenter']).toHaveBeenCalled();
    expect(app.projects()).toEqual([created]);
    expect(app.projectsCenter()).toEqual([created]);
    expect(app.projectsCenterTotal()).toBe(1);
  });

  it('should render the AgentBoard shell', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    app.authVisible.set(false);
    app.view.set('projects');  // Epic 150 X1: home view hides outer topbar/sidebar
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.logo-text')?.textContent).toContain('AgentBoard');
  });

  it('should keep theme switching available without rendering an inert project search', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('project');
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('#theme-toggle')).not.toBeNull();
    expect(element.querySelector('.workspace-project-search')).toBeNull();
  });

  it('should collapse the desktop sidebar into a persistent icon rail', async () => {
    localStorage.removeItem('agentboard_sidebar_collapsed');
    const width = vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1280);
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('projects');  // Epic 150 X1: home view hides outer sidebar
    fixture.detectChanges();

    app.toggleSidebar();
    fixture.detectChanges();

    expect(app.sidebarOpen()).toBe(true);
    expect(app.sidebarCollapsed()).toBe(true);
    expect(localStorage.getItem('agentboard_sidebar_collapsed')).toBe('true');
    expect((fixture.nativeElement as HTMLElement).querySelector('#sidebar')?.classList.contains('sidebar--collapsed')).toBe(true);
    expect((fixture.nativeElement as HTMLElement).querySelector('#sidebar-toggle')?.getAttribute('aria-label')).toBe('展开侧栏');

    width.mockRestore();
    localStorage.removeItem('agentboard_sidebar_collapsed');
  });

  it('should render the simplified story task controls without duplicate status UI', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    const now = '2026-08-13T00:00:00';
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('story');
    app.storyTab.set('list');
    app.project.set({ id: 1, name: 'AgentBoard', key: 'AB', description: '', is_private: false, created_at: now } satisfies Project);
    app.epic.set({ id: 2, project_id: 1, title: 'UX cleanup', description: '', status: 'in_progress', created_at: now } satisfies Epic);
    app.story.set({ id: 3, epic_id: 2, title: 'Simplify task page', description: '', status: 'in_progress', needs_design: false, created_at: now } satisfies Story);
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.taskbar')?.textContent).toContain('新建任务');
    expect(element.querySelector('.legend')).toBeNull();

    // v7.3: 低频控件收进「选项」popover，默认收起
    expect(element.querySelector('.task-opts-popover')).toBeNull();
    expect(app.taskOptionsActive() === true || app.taskOptionsActive() === false).toBe(true);
    app.toggleTaskOptions();
    fixture.detectChanges();
    const popover = element.querySelector('.task-opts-popover');
    expect(popover).not.toBeNull();
    expect(popover?.textContent).toContain('只看我');
    expect(popover?.textContent).toContain('排序');
    expect(popover?.textContent).toContain('分组');
    expect(popover?.textContent).toContain('预设');
    expect(popover?.textContent).toContain('导出');
    expect(popover?.querySelectorAll('.toggle')).toHaveLength(1); // 只看我（唯一 toggle，无重复状态 UI）
    expect(popover?.querySelector('#densityToggle')).not.toBeNull();

    // 关闭 popover 后再次打开仍可（开合状态正常）
    app.closeTaskOptions();
    fixture.detectChanges();
    expect(element.querySelector('.task-opts-popover')).toBeNull();

    app.boardMode.set(true);
    app.toggleTaskOptions();
    fixture.detectChanges();
    expect(element.querySelector('#densityToggle')).toBeNull();
    expect(element.querySelector('#boardToggle')?.classList).toContain('is-active');
  });

  it('should hide technical health controls and render the enterprise user menu', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('projects');  // Epic 150 X1: home view hides outer topbar (user menu)
    app.currentUser.set('alice');
    app.isAdmin.set(true);
    app.showUserMenu.set(true);
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('[title="API 健康状态"]')).toBeNull();
    expect(element.querySelector('#perf-toggle')).toBeNull();
    expect(element.querySelector('.user-avatar')?.textContent).toContain('A');
    expect(element.querySelector('.user-dropdown')?.textContent).toContain('项目空间');
    expect(element.querySelector('.user-dropdown')?.textContent).toContain('管理员后台');
    expect(element.querySelector('.user-dropdown')?.textContent).toContain('命令面板');
    expect(element.querySelector('.user-dropdown')?.textContent).toContain('快捷操作');
    expect(element.querySelector('.user-dropdown')?.textContent).toContain('个人设置');
    expect(element.querySelector('#shortcuts-toggle')).toBeNull();
    expect(element.querySelector('#command-palette-toggle')).toBeNull();
  });

  it('should compute dashboard analytics data (Epic 117 — chart code retained, DOM removed in X1 PR 2)', async () => {
    // Epic 150 / Story 322 X1 PR 2 removed the legacy dashboard <div> from home view
    // and replaced it with HomeShellComponent. The chart data computed functions
    // (dashboardStatusChart / dashboardActivity / dashboardProjectProgress) are still
    // retained on App for backward-compat; only the template rendering was dropped.
    // This test now verifies the data layer, not the DOM.
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('home');
    app.projects.set([{
      id: 7,
      name: 'Analytics Project',
      key: 'AP',
      description: '',
      is_private: false,
      created_at: '2026-08-01T00:00:00',
    }]);
    const now = new Date().toISOString();
    const baseTask: Task = {
      id: 1,
      project_id: 7,
      story_id: 1,
      sprint_id: null,
      type: 'dev',
      title: 'Dashboard task',
      status: 'done',
      priority: 'medium',
      description: '',
      spec: '',
      source_spec_id: null,
      due_date: null,
      assignee_id: null,
      labels: '[]',
      estimate: null,
      created_at: now,
      updated_at: now,
    };
    app.tasks.set([baseTask, { ...baseTask, id: 2, status: 'in_progress' }]);
    fixture.detectChanges();

    // Data layer still computes (used by future chart revivals)
    expect(app.dashboardStatusChart().segments.length).toBeGreaterThan(0);
    expect(app.dashboardActivity().total).toBe(2);
  });

  it('should prefer overview aggregate stats when available (Epic 117)', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('home');
    app.projects.set([{ id: 7, name: 'Analytics Project', key: 'AP', description: '', is_private: false, created_at: '2026-08-01T00:00:00' }]);
    // 模拟 overview 返回（跨项目聚合：2 项目 / 4 任务 / 3 done）
    app.overviewStats.set({
      counts: { projects: 2, epics: 3, stories: 4, tasks: 4, done_tasks: 3 },
      projects: [
        { id: 7, name: 'Analytics Project', total: 3, done: 3, percent: 100 },
        { id: 8, name: 'Empty Project', total: 1, done: 0, percent: 0 },
      ],
      status_distribution: [
        // Story 265：状态收敛为 5 值
        { status: 'todo', count: 0 },
        { status: 'in_progress', count: 0 },
        { status: 'in_review', count: 0 },
        { status: 'blocked', count: 0 },
        { status: 'done', count: 3 },
      ],
      activity_7d: [
        { day: '2026-08-01', count: 0 },
        { day: '2026-08-02', count: 0 },
        { day: '2026-08-03', count: 0 },
        { day: '2026-08-04', count: 0 },
        { day: '2026-08-05', count: 2 },
        { day: '2026-08-06', count: 1 },
        { day: '2026-08-07', count: 0 },
      ],
    });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    // 统计卡直接读 overview counts，不依赖整树
    expect(app.statProjects()).toBe(2);
    expect(app.statEpics()).toBe(3);
    expect(app.statStories()).toBe(4);
    expect(app.statTasks()).toBe(4);
    expect(app.doneTasks()).toBe(3);
    expect(app.dashboardStatusChart().total).toBe(4);
    expect(app.dashboardStatusChart().segments).toHaveLength(1);
    expect(app.dashboardProjectProgress().length).toBe(2);
    expect(app.dashboardProjectProgress()[0].percent).toBe(100);
    expect(app.dashboardActivity().total).toBe(3);
    // Epic 150 X1 PR 2 removed .hero / .stat-number from home view; only data layer
    // (statProjects / doneTasks / dashboard*) is verified now. The HomeShellComponent
    // (line 321 of app.html) renders the Master-Detail master count instead.
    const element = fixture.nativeElement as HTMLElement;
    // HomeShell's project master rows reflect the project count
    const homeShell = element.querySelector('app-home-shell');
    expect(homeShell).not.toBeNull();
  });

  it('should open the standalone notification center in a new browser tab', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    const focus = vi.fn();
    const open = vi.spyOn(window, 'open').mockReturnValue({ focus } as unknown as Window);

    app.openNotificationsTab();

    expect(open).toHaveBeenCalledWith(
      expect.stringContaining('/notifications'),
      '_blank',
    );
    expect((open.mock.results[0].value as Window).opener).toBeNull();
    expect(focus).toHaveBeenCalled();
    open.mockRestore();
  });

  it('should render notifications as a full page', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('notifications');
    app.notifications.set([{
      id: 21,
      user_id: 1,
      type: 'task_assigned',
      title: 'Review the proposal',
      content: 'A task has been assigned to you.',
      is_read: false,
      link: '/task/1',
      created_at: '2026-08-01T00:00:00',
    }]);
    app.unreadCount.set(1);
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.notification-page')).not.toBeNull();
    expect(element.textContent).toContain('通知中心');
    expect(element.textContent).toContain('Review the proposal');
  });

  it('should render the user settings console', async () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.componentInstance.authVisible.set(false);
    fixture.componentInstance.loading.set(false);
    fixture.componentInstance.view.set('settings');
    fixture.componentInstance.profile.set({
      id: 1, username: 'alice', display_name: 'Alice', email: 'alice@example.com',
      avatar_url: null, is_admin: false, created_at: '2026-07-16T00:00:00',
    });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('个人设置');
    expect(text).toContain('我的项目');
    expect(text).toContain('API Key');
  });

  it.skip('should load a project tab on first selection and reuse the loaded data', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    const api = TestBed.inject(ApiService) as ApiService & {
      listSprints: ReturnType<typeof vi.fn>;
    };
    const sprints = new Subject<Sprint[]>();
    const listSprints = vi.fn((_projectId: number) => sprints.asObservable());
    api.listSprints = listSprints;
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('project');
    app.project.set({
      id: 7,
      name: 'Lazy project',
      key: 'LP',
      description: '',
      is_private: false,
      created_at: '2026-07-19T00:00:00',
    });

    app.selectProjectTab('sprints');
    fixture.detectChanges();
    expect(app.isProjectTabLoading('sprints')).toBe(true);
    expect(fixture.nativeElement.querySelector('.tab-list-skeleton')).not.toBeNull();

    sprints.next([
      {
        id: 1,
        project_id: 7,
        title: 'Sprint 1',
        goal: '',
        status: 'planning' as const,
        start_date: null,
        end_date: null,
        created_at: '2026-07-19T00:00:00',
        updated_at: '2026-07-19T00:00:00',
      },
    ]);
    sprints.complete();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(app.sprints()).toHaveLength(1);
    expect(app.isProjectTabLoaded('sprints')).toBe(true);
    expect(fixture.nativeElement.querySelector('.tab-list-skeleton')).toBeNull();

    app.selectProjectTab('sprints');
    await fixture.whenStable();
    expect(listSprints).toHaveBeenCalledTimes(1);
  });

  it('should scope proposals to the active project and lock creation to it', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    const api = TestBed.inject(ApiService) as ApiService & {
      listProposals: ReturnType<typeof vi.fn>;
    };
    const proposals = new Subject<ProposalItem[]>();
    api.listProposals = vi.fn(() => proposals.asObservable());
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('project');
    app.project.set({
      id: 7,
      name: 'Project Proposal Scope',
      key: 'PPS',
      description: '',
      is_private: false,
      created_at: '2026-08-01T00:00:00',
    });
    (api as any).getProject = vi.fn(() => of(app.project()!));
    (api as any).listAgents = vi.fn(() => of([]));

    app.selectProjectTab('proposals');
    expect(api.listProposals).toHaveBeenCalledWith({ project_id: 7, limit: 200 });
    proposals.next([{
      id: 11,
      project_id: 7,
      title: 'Project-only proposal',
      content: 'Scoped requirement',
      status: 'draft',
      current_round: 0,
      converged_spec: '',
      story_id: null,
      auto_create_ticket: false,
      target_epic_id: null,
      ticket_type: '',
      ticket_id: null,
      author_id: 1,
      error: '',
      created_at: '2026-08-01T00:00:00',
      updated_at: '2026-08-01T00:00:00',
    }]);
    proposals.complete();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(app.proposals()).toHaveLength(1);
    expect(app.isProjectTabLoaded('proposals')).toBe(true);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Project-only proposal');
    expect((fixture.nativeElement as HTMLElement).querySelector('#proposal-create-dialog')).toBeNull();

    const createButton = (fixture.nativeElement as HTMLElement).querySelector('#new-project-proposal-btn') as HTMLButtonElement;
    createButton.click();
    fixture.detectChanges();
    const dialog = (fixture.nativeElement as HTMLElement).querySelector('#proposal-create-dialog');
    const projectField = (fixture.nativeElement as HTMLElement).querySelector('#proposal-project') as HTMLInputElement;
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute('role')).toBe('dialog');
    expect(dialog?.getAttribute('aria-modal')).toBe('true');
    expect(app.proposalNewProjectId()).toBe(7);
    expect(projectField.value).toBe('Project Proposal Scope');
    expect(projectField.readOnly).toBe(true);
  });

  it('should scope documents to the active project and lock creation to it', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    const api = TestBed.inject(ApiService) as ApiService & {
      listDocuments: ReturnType<typeof vi.fn>;
      listEpics: ReturnType<typeof vi.fn>;
    };
    const documents = new Subject<DocumentItem[]>();
    api.listDocuments = vi.fn(() => documents.asObservable());
    api.listEpics = vi.fn(() => of([]));
    app.authVisible.set(false);
    app.loading.set(false);
    app.view.set('project');
    app.project.set({
      id: 7,
      name: 'Project Document Scope',
      key: 'PDS',
      description: '',
      is_private: false,
      created_at: '2026-08-01T00:00:00',
    });

    app.selectProjectTab('documents');
    expect(api.listDocuments).toHaveBeenCalledWith({ project_id: 7 });
    documents.next([]);
    documents.complete();
    await fixture.whenStable();

    await app.openDocModal('create');
    fixture.detectChanges();
    const projectField = (fixture.nativeElement as HTMLElement).querySelector('#document-project') as HTMLInputElement;
    expect(app.docCreateProjectId()).toBe(7);
    expect(projectField.value).toBe('Project Document Scope');
    expect(projectField.readOnly).toBe(true);
  });

  it('should use the in-app confirmation dialog and show its busy state', async () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    const api = TestBed.inject(ApiService) as ApiService & {
      deleteSprint: ReturnType<typeof vi.fn>;
    };
    const deletion = new Subject<{ ok: boolean }>();
    api.deleteSprint = vi.fn((_sprintId: number) => deletion.asObservable());

    fixture.detectChanges();
    app.authVisible.set(false);
    app.loading.set(false);
    app.deleteSprint(12);
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('[role="alertdialog"]')).not.toBeNull();
    expect(element.querySelector('#confirmation-title')?.textContent).toContain('删除 Sprint');

    (element.querySelector('#confirmation-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.deleteSprint).toHaveBeenCalledWith(12);
    expect(app.confirmationBusy()).toBe(true);
    expect(element.querySelector('#confirmation-primary')?.textContent).toContain('处理中');

    deletion.next({ ok: true });
    deletion.complete();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(app.confirmation()).toBeNull();
    expect(element.querySelector('[role="alertdialog"]')).toBeNull();
  });

  describe('renderMarkdown 图片渲染（Epic 64 S2）', () => {
    const render = (src: string): string => {
      const fixture = TestBed.createComponent(App);
      return fixture.componentInstance.renderMarkdown(src);
    };

    it('应该把 https 图片语法渲染为 <img>', () => {
      const html = render('看架构图：\n\n![架构](https://cos.example.com/arch.png?x=1&y=2)');
      expect(html).toContain('<img src="https://cos.example.com/arch.png?x=1&amp;y=2"');
      expect(html).toContain('alt="架构"');
      expect(html).toContain('loading="lazy"');
      expect(html).toContain('referrerpolicy="no-referrer"');
    });

    it('应该拒绝 javascript:/data: 等危险协议的图片', () => {
      const html = render('![x](javascript:alert(1)) ![](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)');
      expect(html).not.toContain('<img');
      expect(html).toContain('javascript:alert(1)'); // 原文保留，不执行
    });

    it('应该拒绝属性逃逸注入（onerror 等）并保留原文', () => {
      const html = render('![x](https://ok.com/a.png" onerror="alert(1))');
      expect(html).not.toContain('<img');
      expect(html).toContain('![x](https://ok.com/a.png'); // 原文以纯文本保留，onerror 不会被解析为属性
    });

    it('应该拒绝带控制字符/引号的 URL 并保留原文', () => {
      const html = render("![x](https://ok.com/a' onload='alert(2))");
      expect(html).not.toContain('<img');
      expect(html).toContain('onload');
    });

    it('评论场景：图片与加粗/代码/多行共存（Epic 64 S3）', () => {
      const html = render('评审图 ![截图](https://cos.example.com/r1.png) **加粗** `code` 混合内容\n\n第二行 ![图2](https://cos.example.com/r2.png)');
      const imgs = html.match(/<img /g);
      expect(imgs?.length).toBe(2);
      expect(html).toContain('<strong>加粗</strong>');
      expect(html).toContain('<code>code</code>');
    });

    it('描述场景：标题+图片+列表共存，危险图片拒绝（Epic 64 S4）', () => {
      const html = render('# 描述\n\n![架构图](https://cos.example.com/arch.png)\n\n- 项一\n- ![坏](javascript:alert(9))\n\n![数据](data:image/png;base64,xx)');
      expect(html).toContain('<h1>描述</h1>');
      expect(html).toContain('<img src="https://cos.example.com/arch.png"');
      expect(html).toContain('<ul>');
      expect((html.match(/<img /g) || []).length).toBe(1); // 危险协议不渲染
    });

    it('空 alt 图片应输出空 alt 属性（S3/S4 边界）', () => {
      const html = render('![](https://cos.example.com/no-alt.png)');
      expect(html).toContain('<img src="https://cos.example.com/no-alt.png" alt=""');
    });
  });

  describe('renderMarkdown 链接渲染与 XSS 防护（Epic 145 B-A6）', () => {
    const render = (src: string): string => {
      const fixture = TestBed.createComponent(App);
      return fixture.componentInstance.renderMarkdown(src);
    };

    it('应该把 https 链接渲染为 <a target=_blank rel=noopener>', () => {
      const html = render('参见 [文档](https://example.com/doc/intro)');
      expect(html).toContain('<a href="https://example.com/doc/intro" target="_blank" rel="noopener">文档</a>');
    });

    it('应该保留 URL 中的 & 为 &amp; 实体（不双转义）', () => {
      const html = render('搜索 [q](https://example.com/s?a=1&b=2)');
      expect(html).toContain('<a href="https://example.com/s?a=1&amp;b=2"');
      expect(html).not.toContain('href="https://example.com/s?a=1&b=2"');
    });

    it('应该拒绝 javascript: 协议的链接（正则强制 https?://，原文保留）', () => {
      const html = render('点 [这里](javascript:alert(1))');
      expect(html).not.toContain('<a ');
      expect(html).toContain('javascript:alert(1)'); // 原文以纯文本保留
    });

    it('应该拒绝 URL 中的双引号（属性逃逸注入）并保留原文', () => {
      const html = render('[x](https://a.com/" onclick="alert(1))');
      expect(html).not.toContain('<a ');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
      expect(html).toContain('[x]('); // 原文以纯文本保留
    });

    it('应该拒绝标签模板注入攻击向量（无需 ) 的 JS 执行）', () => {
      // 攻击向量：[x](https://a.com/"onclick="alert`1`) —— `"` 闭合 href，
      // 浏览器容错解析出 onclick，标签模板 `alert\`1\`` 无需 `)` 即可执行
      const html = render('[x](https://a.com/"onclick="alert`1`)');
      expect(html).not.toContain('<a ');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
      // 危险 URL 原文以纯文本保留（backtick 可能被行内代码格式消费，但不会成为可执行属性）
      expect(html).toContain('[x](');
    });

    it('应该拒绝 URL 中的单引号并保留原文', () => {
      const html = render("[x](https://a.com/'onclick='alert(1))");
      expect(html).not.toContain('<a ');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
    });

    it('链接文本中的 < > 应被转义（来自 esc 预处理）', () => {
      const html = render('[<b>bold</b>](https://example.com/x)');
      expect(html).toContain('&lt;b&gt;');
      expect(html).not.toContain('<b>bold</b>');
    });

    it('应该拒绝带空白的 URL（正则已排除 \\s，纵深防御）', () => {
      const html = render('[x](https://a.com/ onclick=alert(1))');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
    });

    it('评论场景：链接 + 图片 + 代码 + 加粗共存且各自安全', () => {
      const html = render('评审 [链接](https://ok.com/a?x=1&y=2) ![图](https://cos.example.com/r.png) `code` **粗**');
      expect(html).toContain('<a href="https://ok.com/a?x=1&amp;y=2"');
      expect(html).toContain('<img src="https://cos.example.com/r.png"');
      expect(html).toContain('<code>code</code>');
      expect(html).toContain('<strong>粗</strong>');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
    });

    it('描述场景：标题 + 列表 + 安全链接 + 危险链接混合', () => {
      const html = render('# 标题\n\n- 安全 [ok](https://ok.com)\n- 危险 [bad](https://a.com/"onclick="alert`1`)');
      expect(html).toContain('<h1>标题</h1>');
      expect(html).toContain('<a href="https://ok.com"');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
      // 危险链接原文保留
      expect(html).toContain('[bad](');
    });

    it('代码块内的伪链接不应被渲染为 <a>（先消费围栏）', () => {
      const html = render('```\n[x](https://a.com/"onclick="alert`1`)\n```');
      expect(html).toContain('<pre class="code-block">');
      expect(html).not.toContain('<a ');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
    });

    it('行内代码内的伪链接不应被渲染为 <a>', () => {
      const html = render('代码 `[x](https://a.com/"onclick="alert\`1\`)` 结束');
      expect(html).not.toContain('<a ');
      expect(html).not.toMatch(/<[^>]*\bon\w+\s*=/i); // 无任何标签含 on* 事件属性（精确 XSS 断言）
    });
  });

  describe('loadDashboardFullTree 请求风暴治理（Epic 117 S2 / Task 996）', () => {
    const ep1: Epic = { id: 11, project_id: 7, title: 'Epic A', description: '', status: 'in_progress', created_at: '2026-08-01T00:00:00' };
    const ep2: Epic = { id: 12, project_id: 7, title: 'Epic B', description: '', status: 'backlog', created_at: '2026-08-01T00:00:00' };
    const st1: Story = { id: 21, epic_id: 11, title: 'Story A1', description: '', status: 'todo', needs_design: false, created_at: '2026-08-01T00:00:00' };
    const st2: Story = { id: 22, epic_id: 12, title: 'Story B1', description: '', status: 'in_review', needs_design: false, created_at: '2026-08-01T00:00:00' };
    const overview: OverviewStats = {
      counts: { projects: 1, epics: 2, stories: 2, tasks: 3, done_tasks: 1 },
      projects: [{ id: 7, name: 'Analytics Project', total: 3, done: 1, percent: 33.3 }],
      status_distribution: [{ status: 'done', count: 1 }, { status: 'todo', count: 2 }],
      activity_7d: [{ day: '2026-08-05', count: 1 }],
    };

    function createAppWithApi(overrides: Record<string, unknown>): { app: any; listTasks: ReturnType<typeof vi.fn> } {
      const listTasks = vi.fn(() => of([] as Task[]));
      const apiMock = {
        baseUrl: 'http://test',
        listProjects: () => of([]),
        getOverview: () => of(overview),
        listEpics: (pid: number) => of(pid === 7 ? [ep1, ep2] : []),
        listStories: (eid: number) => of(eid === 11 ? [st1] : eid === 12 ? [st2] : []),
        listTasks,
        ...overrides,
      };
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance as any;
      app.api = apiMock;
      return { app, listTasks };
    }

    it('overview 成功时跳过 Task 级全量加载（listTasks 零调用），epics/stories 仍填充', async () => {
      const { app, listTasks } = createAppWithApi({});
      app.projects.set([{ id: 7, name: 'Analytics Project', key: 'AP', description: '', is_private: false, created_at: '2026-08-01T00:00:00' }]);
      app.overviewStats.set(overview);
      app.view.set('home');
      app.routeLoadGeneration = 1;

      await app.loadDashboardFullTree(1);

      expect(listTasks).not.toHaveBeenCalled();
      expect(app.epics()).toEqual([ep1, ep2]);
      expect(app.stories()).toEqual([st1, st2]);
      expect(app.tasks()).toEqual([]);
    });

    it('overview 失败（null）时保留全量回退：Task 级被调用并填充 tasks()', async () => {
      const { app, listTasks } = createAppWithApi({});
      app.projects.set([{ id: 7, name: 'Analytics Project', key: 'AP', description: '', is_private: false, created_at: '2026-08-01T00:00:00' }]);
      app.overviewStats.set(null); // 阶段一失败 → 全量回退
      app.view.set('home');
      app.routeLoadGeneration = 1;

      await app.loadDashboardFullTree(1);

      expect(listTasks).toHaveBeenCalledTimes(2); // st1 + st2 各一次
      expect(app.tasks()).toEqual([]); // listTasks 返回空数组
    });

    it('parallelMap 并发上限不超过 limit 且保留输入顺序', async () => {
      const { app } = createAppWithApi({});
      let active = 0;
      let peak = 0;
      const fn = vi.fn(async (x: number) => {
        active++;
        peak = Math.max(peak, active);
        await new Promise((r) => setTimeout(r, 15));
        active--;
        return x * 2;
      });
      const result = await app.parallelMap([1, 2, 3, 4, 5, 6, 7, 8], 3, fn);
      expect(peak).toBeLessThanOrEqual(3);
      expect(result).toEqual([2, 4, 6, 8, 10, 12, 14, 16]);
      expect(fn).toHaveBeenCalledTimes(8);
    });

    it('parallelMap 单项失败跳过、成功项保留，不中断整段', async () => {
      const { app } = createAppWithApi({});
      const fn = vi.fn(async (x: number) => {
        if (x === 2) throw new Error('boom');
        return x;
      });
      const result = await app.parallelMap([1, 2, 3, 4], 2, fn);
      expect(result).toEqual([1, 3, 4]);
    });
  });

  describe('loadEpicProgressData 并发分片治理（Epic 117 S3 / Task 997）', () => {
    const ep1: Epic = { id: 31, project_id: 7, title: 'Epic X', description: '', status: 'in_progress', created_at: '2026-08-01T00:00:00' };
    const ep2: Epic = { id: 32, project_id: 7, title: 'Epic Y', description: '', status: 'backlog', created_at: '2026-08-01T00:00:00' };
    const st1: Story = { id: 41, epic_id: 31, title: 'Story X1', description: '', status: 'todo', needs_design: false, created_at: '2026-08-01T00:00:00' };
    const st2: Story = { id: 42, epic_id: 32, title: 'Story Y1', description: '', status: 'in_review', needs_design: false, created_at: '2026-08-01T00:00:00' };
    const t1: Task = { id: 51, project_id: 7, story_id: 41, sprint_id: null, type: 'dev', title: 'Task X1-1', status: 'done', priority: 'medium', description: '', spec: '', source_spec_id: null, due_date: null, assignee_id: null, labels: '[]', estimate: null, created_at: '2026-08-01T00:00:00', updated_at: '2026-08-01T00:00:00' };

    function createProjectApp(overrides: Record<string, unknown>): { app: any; listStories: ReturnType<typeof vi.fn>; listTasks: ReturnType<typeof vi.fn> } {
      const listStories = vi.fn((eid: number) => of(eid === 31 ? [st1] : eid === 32 ? [st2] : []));
      const listTasks = vi.fn((sid: number) => of(sid === 41 ? [t1] : []));
      const apiMock = {
        baseUrl: 'http://test',
        listProjects: () => of([]),
        getOverview: () => of(null as unknown as OverviewStats),
        listEpics: (pid: number) => of(pid === 7 ? [ep1, ep2] : []),
        listStories,
        listTasks,
        ...overrides,
      };
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance as any;
      app.api = apiMock;
      app.project.set({ id: 7, name: 'Analytics Project', key: 'AP', description: '', is_private: false, created_at: '2026-08-01T00:00:00' });
      app.projectTabGeneration = 1;
      app.view.set('project');
      return { app, listStories, listTasks };
    }

    it('两级加载均使用分片：并发上限不超过 6', async () => {
      const { app } = createProjectApp({});
      // 覆盖 listStories 为带并发计数的延迟 Observable，观测峰值并发
      let active = 0;
      let peak = 0;
      app.api.listStories = vi.fn((eid: number) => {
        active++;
        peak = Math.max(peak, active);
        return of(eid === 31 ? [st1] : eid === 32 ? [st2] : []).pipe(
          delay(20),
          tap(() => {
            active--;
          }),
        );
      });
      await app.loadEpicProgressData(7, [ep1, ep2], 1);
      expect(peak).toBeLessThanOrEqual(6);
      expect(app.stories()).toEqual([st1, st2]);
      expect(app.tasks()).toEqual([t1]);
    });

    it('单项失败跳过、成功项保留，不中断整段（任务级仍填充）', async () => {
      const { app } = createProjectApp({});
      // ep2 的 listStories 失败 → stories 保留 st1，tasks 继续加载 st1 的任务
      app.api.listStories = vi.fn((eid: number) => {
        if (eid === 32) throw new Error('boom');
        return of([st1]);
      });
      await app.loadEpicProgressData(7, [ep1, ep2], 1);
      expect(app.stories()).toEqual([st1]);
      expect(app.tasks()).toEqual([t1]);
    });

    it('story 视图不写全局 tasks()（契约不变）', async () => {
      const { app, listTasks } = createProjectApp({});
      app.view.set('story');
      await app.loadEpicProgressData(7, [ep1, ep2], 1);
      expect(listTasks).toHaveBeenCalled();
      expect(app.tasks()).toEqual([]);
    });
  });

  describe('Epic 120 v6.14 命令面板 Sprint 搜索', () => {
    it('paletteItems 合并 Sprint 搜索结果（category=sprint）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('迭代');
      app.paletteSprintResults.set([
        {
          id: 'sprint-5',
          title: 'Sprint #5：迭代发布',
          hint: 'AgentBoard · planning',
          category: 'sprint',
          keywords: 'sprint 5',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const sprint = items.find((i) => i.id === 'sprint-5');
      expect(sprint).toBeTruthy();
      expect(sprint?.category).toBe('sprint');
      // 短查询清空分支：<2 字符 → Sprint 结果清空
      app.paletteRunSearch('x');
      expect(app.paletteSprintResults()).toEqual([]);
    });

    it('渲染 Sprint 分类标签（.cat-sprint → "Sprint"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('迭代');
      app.paletteSprintResults.set([
        {
          id: 'sprint-5',
          title: 'Sprint #5：迭代发布',
          hint: 'AgentBoard · planning',
          category: 'sprint',
          keywords: 'sprint 5',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-sprint');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('Sprint');
      expect(el.textContent).toContain('Sprint #5：迭代发布');
      app.paletteOpen.set(false);
    });
  });

  describe('Epic 121 v6.15 命令面板通知搜索', () => {
    it('paletteItems 合并通知搜索结果（category=notification）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('分配');
      app.paletteNotificationResults.set([
        {
          id: 'notification-9',
          title: '通知 #9：任务 #101 已分配给你',
          hint: '任务分配 · 未读 · /task/101',
          category: 'notification',
          keywords: 'notification 通知 9',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const notif = items.find((i) => i.id === 'notification-9');
      expect(notif).toBeTruthy();
      expect(notif?.category).toBe('notification');
      // 短查询清空分支：<2 字符 → 通知结果清空
      app.paletteRunSearch('x');
      expect(app.paletteNotificationResults()).toEqual([]);
    });

    it('渲染通知分类标签（.cat-notification → "通知"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('分配');
      app.paletteNotificationResults.set([
        {
          id: 'notification-9',
          title: '通知 #9：任务 #101 已分配给你',
          hint: '任务分配 · 未读 · /task/101',
          category: 'notification',
          keywords: 'notification 通知 9',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-notification');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('通知');
      expect(el.textContent).toContain('通知 #9：任务 #101 已分配给你');
      app.paletteOpen.set(false);
    });

    it('openPalette / closePalette 清空通知结果', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteNotificationResults.set([{ id: 'n1', title: 't', category: 'notification', run: () => {} }]);
      app.openPalette();
      expect(app.paletteNotificationResults()).toEqual([]);
      app.paletteNotificationResults.set([{ id: 'n1', title: 't', category: 'notification', run: () => {} }]);
      app.closePalette();
      expect(app.paletteNotificationResults()).toEqual([]);
    });
  });

  describe('Epic 131 v6.16 命令面板 Agent 搜索', () => {
    it('paletteItems 合并 Agent 搜索结果（category=agent）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('wb-dev');
      app.paletteAgentResults.set([
        {
          id: 'agent-1',
          title: 'Agent wb-dev-1：Dev Worker One',
          hint: '在线 · OK v1.2.3',
          category: 'agent',
          keywords: 'agent wb-dev-1 Dev Worker One',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const ag = items.find((i) => i.id === 'agent-1');
      expect(ag).toBeTruthy();
      expect(ag?.category).toBe('agent');
      // 短查询清空分支：<2 字符 → Agent 结果清空
      app.paletteRunSearch('x');
      expect(app.paletteAgentResults()).toEqual([]);
    });

    it('渲染 Agent 分类标签（.cat-agent → "Agent"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('wb-dev');
      app.paletteAgentResults.set([
        {
          id: 'agent-1',
          title: 'Agent wb-dev-1：Dev Worker One',
          hint: '在线 · OK v1.2.3',
          category: 'agent',
          keywords: 'agent wb-dev-1 Dev Worker One',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-agent');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('Agent');
      expect(el.textContent).toContain('Agent wb-dev-1：Dev Worker One');
      app.paletteOpen.set(false);
    });

    it('openPalette / closePalette 清空 Agent 结果', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteAgentResults.set([{ id: 'a1', title: 't', category: 'agent', run: () => {} }]);
      app.openPalette();
      expect(app.paletteAgentResults()).toEqual([]);
      app.paletteAgentResults.set([{ id: 'a1', title: 't', category: 'agent', run: () => {} }]);
      app.closePalette();
      expect(app.paletteAgentResults()).toEqual([]);
    });
  });

  describe('Epic 132 v6.17 命令面板 Proposal 搜索', () => {
    it('paletteItems 合并 Proposal 搜索结果（category=proposal）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('Zebra');
      app.paletteProposalResults.set([
        {
          id: 'proposal-1',
          title: 'Proposal #1：Zebra 导入工具',
          hint: 'AgentBoard · 待开始',
          category: 'proposal',
          keywords: 'proposal 1 Zebra 导入工具',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const pp = items.find((i) => i.id === 'proposal-1');
      expect(pp).toBeTruthy();
      expect(pp?.category).toBe('proposal');
      // 短查询清空分支：<2 字符 → Proposal 结果清空
      app.paletteRunSearch('x');
      expect(app.paletteProposalResults()).toEqual([]);
    });

    it('渲染 Proposal 分类标签（.cat-proposal → "Proposal"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('Zebra');
      app.paletteProposalResults.set([
        {
          id: 'proposal-1',
          title: 'Proposal #1：Zebra 导入工具',
          hint: 'AgentBoard · 待开始',
          category: 'proposal',
          keywords: 'proposal 1 Zebra 导入工具',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-proposal');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('Proposal');
      expect(el.textContent).toContain('Proposal #1：Zebra 导入工具');
      app.paletteOpen.set(false);
    });

    it('openPalette / closePalette 清空 Proposal 结果', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteProposalResults.set([{ id: 'p1', title: 't', category: 'proposal', run: () => {} }]);
      app.openPalette();
      expect(app.paletteProposalResults()).toEqual([]);
      app.paletteProposalResults.set([{ id: 'p1', title: 't', category: 'proposal', run: () => {} }]);
      app.closePalette();
      expect(app.paletteProposalResults()).toEqual([]);
    });
  });

  describe('Epic 133 v6.18 命令面板 Ticket 搜索', () => {
    it('paletteItems 合并 Ticket 搜索结果（category=ticket）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('工单');
      app.paletteTicketResults.set([
        {
          id: 'ticket-1',
          title: 'Ticket #1：Zebra 批处理工单',
          hint: 'AgentBoard · task · done',
          category: 'ticket',
          keywords: 'ticket 1 Zebra 批处理工单',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const tk = items.find((i) => i.id === 'ticket-1');
      expect(tk).toBeTruthy();
      expect(tk?.category).toBe('ticket');
      // 短查询清空分支：<2 字符 → Ticket 结果清空
      app.paletteRunSearch('x');
      expect(app.paletteTicketResults()).toEqual([]);
    });

    it('渲染 Ticket 分类标签（.cat-ticket → "Ticket"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('工单');
      app.paletteTicketResults.set([
        {
          id: 'ticket-1',
          title: 'Ticket #1：Zebra 批处理工单',
          hint: 'AgentBoard · task · done',
          category: 'ticket',
          keywords: 'ticket 1 Zebra 批处理工单',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-ticket');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('Ticket');
      expect(el.textContent).toContain('Ticket #1：Zebra 批处理工单');
      app.paletteOpen.set(false);
    });

    it('openPalette / closePalette 清空 Ticket 结果', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteTicketResults.set([{ id: 't1', title: 't', category: 'ticket', run: () => {} }]);
      app.openPalette();
      expect(app.paletteTicketResults()).toEqual([]);
      app.paletteTicketResults.set([{ id: 't1', title: 't', category: 'ticket', run: () => {} }]);
      app.closePalette();
      expect(app.paletteTicketResults()).toEqual([]);
    });
  });

  describe('Epic 134 v6.19 命令面板 Schedule 搜索', () => {
    it('paletteItems 合并 Schedule 搜索结果（category=schedule）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('夜间');
      app.paletteScheduleResults.set([
        {
          id: 'schedule-1',
          title: '计划 #1：夜间构建计划',
          hint: 'AgentBoard · 0 2 * * * · codex',
          category: 'schedule',
          keywords: 'schedule 1 夜间构建计划 codex cron',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const sch = items.find((i) => i.id === 'schedule-1');
      expect(sch).toBeTruthy();
      expect(sch?.category).toBe('schedule');
      // 短查询清空分支：<2 字符 → Schedule 结果清空
      app.paletteRunSearch('x');
      expect(app.paletteScheduleResults()).toEqual([]);
    });

    it('渲染 Schedule 分类标签（.cat-schedule → "计划"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('夜间');
      app.paletteScheduleResults.set([
        {
          id: 'schedule-1',
          title: '计划 #1：夜间构建计划',
          hint: 'AgentBoard · 0 2 * * * · codex',
          category: 'schedule',
          keywords: 'schedule 1 夜间构建计划',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-schedule');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('计划');
      expect(el.textContent).toContain('计划 #1：夜间构建计划');
      app.paletteOpen.set(false);
    });

    it('openPalette / closePalette 清空 Schedule 结果', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteScheduleResults.set([{ id: 's1', title: 't', category: 'schedule', run: () => {} }]);
      app.openPalette();
      expect(app.paletteScheduleResults()).toEqual([]);
      app.paletteScheduleResults.set([{ id: 's1', title: 't', category: 'schedule', run: () => {} }]);
      app.closePalette();
      expect(app.paletteScheduleResults()).toEqual([]);
    });
  });

  describe('Epic 135 v6.20 命令面板 AgentRun 搜索', () => {
    it('paletteItems 合并 Run 搜索结果（category=run）且短查询清空', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteQuery.set('超时');
      app.paletteRunResults.set([
        {
          id: 'run-1',
          title: '运行 #1：构建超时（120s 上限）',
          hint: 'AgentBoard · 失败',
          category: 'run',
          keywords: 'run 1 failed 构建超时',
          run: () => {},
        },
      ]);
      const items = app.paletteItems();
      const run = items.find((i) => i.id === 'run-1');
      expect(run).toBeTruthy();
      expect(run?.category).toBe('run');
      // 短查询清空分支：<2 字符 → Run 结果清空
      app.paletteRunSearch('x');
      expect(app.paletteRunResults()).toEqual([]);
    });

    it('渲染 Run 分类标签（.cat-run → "运行"）', async () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteOpen.set(true);
      app.paletteQuery.set('超时');
      app.paletteRunResults.set([
        {
          id: 'run-1',
          title: '运行 #1：构建超时（120s 上限）',
          hint: 'AgentBoard · 失败',
          category: 'run',
          keywords: 'run 1 failed 构建超时',
          run: () => {},
        },
      ]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const cat = el.querySelector('.palette-item-cat.cat-run');
      expect(cat).toBeTruthy();
      expect(cat?.textContent?.trim()).toBe('运行');
      expect(el.textContent).toContain('运行 #1：构建超时（120s 上限）');
      app.paletteOpen.set(false);
    });

    it('runStatusLabel 映射各状态中文标签', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      expect((app as unknown as { runStatusLabel(s: string): string }).runStatusLabel('pending')).toBe('等待中');
      expect((app as unknown as { runStatusLabel(s: string): string }).runStatusLabel('running')).toBe('运行中');
      expect((app as unknown as { runStatusLabel(s: string): string }).runStatusLabel('success')).toBe('成功');
      expect((app as unknown as { runStatusLabel(s: string): string }).runStatusLabel('failed')).toBe('失败');
      expect((app as unknown as { runStatusLabel(s: string): string }).runStatusLabel('unknown')).toBe('unknown');
    });

    it('openPalette / closePalette 清空 Run 结果', () => {
      const fixture = TestBed.createComponent(App);
      const app = fixture.componentInstance;
      app.paletteRunResults.set([{ id: 'r1', title: 't', category: 'run', run: () => {} }]);
      app.openPalette();
      expect(app.paletteRunResults()).toEqual([]);
      app.paletteRunResults.set([{ id: 'r1', title: 't', category: 'run', run: () => {} }]);
      app.closePalette();
      expect(app.paletteRunResults()).toEqual([]);
    });
  });
});
