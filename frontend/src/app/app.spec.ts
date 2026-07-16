import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { ApiService } from './api.service';
import { App } from './app';
import { routes } from './app.routes';

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
});
