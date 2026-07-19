import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, Subject } from 'rxjs';
import { vi } from 'vitest';

import { ApiService } from './api.service';
import { App } from './app';
import { routes } from './app.routes';
import { Sprint } from './models';

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

  it('should render the AgentBoard shell', async () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    fixture.componentInstance.authVisible.set(false);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.logo-text')?.textContent).toContain('AgentBoard');
  });

  it('should render the user settings console', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    fixture.componentInstance.authVisible.set(false);
    fixture.componentInstance.loading.set(false);
    fixture.componentInstance.view.set('settings');
    fixture.componentInstance.profile.set({
      id: 1, username: 'alice', display_name: 'Alice', email: 'alice@example.com',
      avatar_url: null, is_admin: false, created_at: '2026-07-16T00:00:00',
    });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('个人设置');
    expect(text).toContain('我的项目');
    expect(text).toContain('API Key');
  });

  it('should load a project tab on first selection and reuse the loaded data', async () => {
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
});
