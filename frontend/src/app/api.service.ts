import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, of, throwError, timer } from 'rxjs';
import { catchError, tap, switchMap, mergeMap } from 'rxjs/operators';

import { ApiErrorBody, Attachment, AuthResult, Comment, Epic, Notification, PagedResult, Project, ProjectMember, ProjectStats, Sprint, Story, Task, AgentSchedule, AgentRun, TaskDependencies, AuditLog, WebhookConfig } from './models';

export const AUTH_EXPIRED_EVENT = 'agentboard:auth-expired';

declare global {
  interface Window {
    AGENTBOARD_API?: string;
    AGENTBOARD_STATS_CACHE_TTL?: string;
  }
}

// ========== Simple Cache Layer ==========
interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

class ApiCache {
  private cache = new Map<string, CacheEntry<any>>();
  private readonly DEFAULT_TTL = 30000; // 30 seconds
  private readonly SEARCH_TTL = 30000; // 30 seconds for search

  get<T>(key: string): T | null {
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.timestamp > this.DEFAULT_TTL) {
      this.cache.delete(key);
      return null;
    }
    return entry.data as T;
  }

  getWithTTL<T>(key: string, ttl: number): T | null {
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.timestamp > ttl) {
      this.cache.delete(key);
      return null;
    }
    return entry.data as T;
  }

  set<T>(key: string, data: T): void {
    this.cache.set(key, { data, timestamp: Date.now() });
  }

  invalidate(pattern?: string): void {
    if (!pattern) {
      this.cache.clear();
      return;
    }
    for (const key of this.cache.keys()) {
      if (key.includes(pattern)) {
        this.cache.delete(key);
      }
    }
  }

  invalidatePrefix(prefix: string): void {
    for (const key of this.cache.keys()) {
      if (key.startsWith(prefix)) {
        this.cache.delete(key);
      }
    }
  }
}

const apiCache = new ApiCache();

// ========== Offline Queue (Task 472) ==========
const OFFLINE_QUEUE_KEY = 'agentboard_offline_queue';

interface QueuedRequest {
  id: string;
  method: string;
  path: string;
  body?: unknown;
  params?: Record<string, string | number | undefined>;
  timestamp: number;
}

function getOfflineQueue(): QueuedRequest[] {
  try {
    return JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || '[]');
  } catch { return []; }
}

function saveOfflineQueue(queue: QueuedRequest[]): void {
  localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(queue.slice(0, 50))); // max 50
}

function addToOfflineQueue(req: QueuedRequest): void {
  const queue = getOfflineQueue();
  queue.push(req);
  saveOfflineQueue(queue);
}

export const OFFLINE_QUEUE_FLUSH_EVENT = 'agentboard:flush-offline-queue';

@Injectable({ providedIn: 'root' })
export class ApiService {
  readonly baseUrl = window.AGENTBOARD_API || 'http://127.0.0.1:8000';
  private _isOnline = navigator.onLine;
  private _retryCount = 3; // Task 470: max retries for exponential backoff

  constructor(private readonly http: HttpClient) {
    window.addEventListener('online', () => {
      this._isOnline = true;
      window.dispatchEvent(new CustomEvent(OFFLINE_QUEUE_FLUSH_EVENT));
    });
    window.addEventListener('offline', () => { this._isOnline = false; });
  }

  // Task 472: flush offline queue when back online
  flushOfflineQueue(httpFn: (req: QueuedRequest) => Observable<any>): void {
    const queue = getOfflineQueue();
    if (!queue.length) return;
    const token = localStorage.getItem('agentboard_token');
    const headers = token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : undefined;
    for (const req of queue) {
      httpFn(req).subscribe({ next: () => {}, error: () => {} });
    }
    saveOfflineQueue([]);
  }

  // Cache invalidation helper
  invalidateCache(pattern?: string): void {
    apiCache.invalidate(pattern);
  }

  invalidateProjectCache(projectId?: number): void {
    if (projectId) {
      apiCache.invalidatePrefix(`/api/projects/${projectId}`);
    } else {
      apiCache.invalidatePrefix('/api/projects');
    }
  }

  private options(params?: Record<string, string | number | undefined>) {
    let httpParams = new HttpParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined) httpParams = httpParams.set(key, String(value));
    }
    const token = localStorage.getItem('agentboard_token');
    const headers = token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : undefined;
    return { params: httpParams, headers };
  }

  private request<T>(
    method: string,
    path: string,
    body?: unknown,
    params?: Record<string, string | number | undefined>,
    _retries = 0,
  ): Observable<T> {
    // Task 472: offline queue — if offline, queue write operations
    if (!this._isOnline && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method.toUpperCase())) {
      const queuedReq: QueuedRequest = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        method, path, body, params,
        timestamp: Date.now(),
      };
      addToOfflineQueue(queuedReq);
      return throwError(() => new Error('离线，操作已加入队列，将在恢复网络后自动重试'));
    }
    return (this.http
      .request(method, `${this.baseUrl}${path}`, { ...this.options(params), body }) as Observable<T>)
      .pipe(
        catchError((error: HttpErrorResponse) => {
          // Task 470: exponential backoff retry on transient server errors (500-503)
          // 不要在 429 上重试：429 是服务器明确说"等一下"，重试会更快耗尽配额
          const retryable = error.status >= 500 && error.status < 504;
          if (retryable && _retries < this._retryCount) {
            const delay = Math.min(1000 * Math.pow(2, _retries), 8000); // 1s, 2s, 4s
            return timer(delay).pipe(
              switchMap(() => this.request<T>(method, path, body, params, _retries + 1))
            );
          }
          if (error.status === 401 && localStorage.getItem('agentboard_token')) {
            localStorage.removeItem('agentboard_token');
            localStorage.removeItem('agentboard_user');
            localStorage.removeItem('agentboard_is_admin');
            window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
          }
          const payload = error.error as ApiErrorBody | undefined;
          const detail = Array.isArray(payload?.detail)
            ? payload?.detail.map((item) => item.msg || '参数错误').join('；')
            : payload?.detail;
          return throwError(() => new Error(detail || error.message || `HTTP ${error.status}`));
        }),
      );
  }

  listProjects() {
    const cacheKey = '/api/projects';
    const cached = apiCache.get<PagedResult<Project>>(cacheKey);
    if (cached) return of(cached);
    return this.request<PagedResult<Project>>('GET', '/api/projects').pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }

  getHealth() {
    return this.http.get<{ status: string; database: string; version: string; timestamp: string }>(
      `${this.baseUrl}/api/health`,
      { headers: new HttpHeaders() }  // no auth header
    );
  }

  getProject(id: number) {
    return this.request<Project>('GET', `/api/projects/${id}`);
  }
  createProject(body: { name: string; key?: string; description?: string }) {
    return this.request<Project>('POST', '/api/projects', body).pipe(
      tap(() => this.invalidateProjectCache())
    );
  }
  updateProject(id: number, body: Partial<Project>) {
    return this.request<Project>('PATCH', `/api/projects/${id}`, body);
  }
  deleteProject(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/projects/${id}`).pipe(
      tap(() => this.invalidateProjectCache())
    );
  }

  listEpics(projectId: number) {
    const cacheKey = `/api/projects/${projectId}/epics`;
    const cached = apiCache.get<Epic[]>(cacheKey);
    if (cached) return of(cached);
    return this.request<Epic[]>('GET', cacheKey).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  getEpic(id: number) {
    return this.request<Epic>('GET', `/api/epics/${id}`);
  }
  createEpic(projectId: number, body: { title: string; description?: string }) {
    return this.request<Epic>('POST', `/api/projects/${projectId}/epics`, body).pipe(
      tap(() => this.invalidateProjectCache(projectId))
    );
  }
  updateEpic(id: number, body: Partial<Epic>) {
    return this.request<Epic>('PATCH', `/api/epics/${id}`, body);
  }
  deleteEpic(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/epics/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/projects'))
    );
  }

  listStories(epicId: number) {
    const cacheKey = `/api/epics/${epicId}/stories`;
    const cached = apiCache.get<Story[]>(cacheKey);
    if (cached) return of(cached);
    return this.request<Story[]>('GET', cacheKey).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  getStory(id: number) {
    return this.request<Story>('GET', `/api/stories/${id}`);
  }
  createStory(epicId: number, body: { title: string; description?: string }) {
    return this.request<Story>('POST', `/api/epics/${epicId}/stories`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/epics'))
    );
  }
  updateStory(id: number, body: Partial<Story>) {
    return this.request<Story>('PATCH', `/api/stories/${id}`, body);
  }
  deleteStory(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/stories/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/epics'))
    );
  }

  listTasks(storyId: number) {
    const cacheKey = `/api/stories/${storyId}/tasks`;
    const cached = apiCache.get<Task[]>(cacheKey);
    if (cached) return of(cached);
    return this.request<Task[]>('GET', cacheKey).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  searchTasks(params: Record<string, string | number | undefined>) {
    // Build cache key from params
    const paramStr = Object.entries(params).filter(([_, v]) => v !== undefined)
      .map(([k, v]) => `${k}=${v}`).sort().join('&');
    const cacheKey = `/api/tasks?${paramStr}`;
    const cached = apiCache.getWithTTL<Task[]>(cacheKey, 30000);
    if (cached) return of(cached);
    return this.request<Task[]>('GET', '/api/tasks', undefined, params).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  getTask(id: number) {
    return this.request<Task>('GET', `/api/tasks/${id}`);
  }
  createTask(storyId: number, body: Partial<Task> & { project_id: number; title: string }) {
    return this.request<Task>('POST', `/api/stories/${storyId}/tasks`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/stories'))
    );
  }
  updateTask(id: number, body: Partial<Task>) {
    return this.request<Task>('PATCH', `/api/tasks/${id}`, body);
  }
  setTaskStatus(id: number, status: string) {
    return this.request<Task>('PUT', `/api/tasks/${id}/status`, { status });
  }
  deleteTask(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/tasks/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/stories'))
    );
  }
  generateSubtasks(id: number) {
    return this.request<Task[]>('POST', `/api/tasks/${id}/generate-subtasks`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/stories'))
    );
  }

  listComments(taskId: number) {
    return this.request<Comment[]>('GET', `/api/tasks/${taskId}/comments`);
  }
  addComment(taskId: number, body: { author: string; content: string }) {
    return this.request<Comment>('POST', `/api/tasks/${taskId}/comments`, body);
  }
  deleteComment(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/comments/${id}`);
  }

  register(username: string, password: string) {
    return this.request<AuthResult>('POST', '/api/auth/register', { username, password });
  }
  login(username: string, password: string) {
    return this.request<AuthResult>('POST', '/api/auth/login', { username, password });
  }

  me() {
    return this.request<{ id: number; username: string; is_admin: boolean }>('GET', '/api/auth/me');
  }

  /* ---------- Sprint ---------- */
  listSprints(projectId: number) {
    const cacheKey = `/api/projects/${projectId}/sprints`;
    const cached = apiCache.get<Sprint[]>(cacheKey);
    if (cached) return of(cached);
    return this.request<Sprint[]>('GET', cacheKey).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  getSprint(id: number) {
    return this.request<Sprint>('GET', `/api/sprints/${id}`);
  }
  createSprint(projectId: number, body: { title: string; goal?: string }) {
    return this.request<Sprint>('POST', `/api/projects/${projectId}/sprints`, body).pipe(
      tap(() => this.invalidateProjectCache(projectId))
    );
  }
  updateSprint(id: number, body: Partial<Sprint>) {
    return this.request<Sprint>('PATCH', `/api/sprints/${id}`, body);
  }
  activateSprint(id: number) {
    return this.request<Sprint>('POST', `/api/sprints/${id}/activate`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/projects'))
    );
  }
  completeSprint(id: number) {
    return this.request<Sprint>('POST', `/api/sprints/${id}/complete`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/projects'))
    );
  }
  deleteSprint(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/sprints/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/projects'))
    );
  }
  listSprintTasks(sprintId: number) {
    return this.request<Task[]>('GET', `/api/sprints/${sprintId}/tasks`);
  }
  getSprintBurndown(sprintId: number) {
    return this.request<any>('GET', `/api/sprints/${sprintId}/burndown`);
  }

  /* ---------- Project Members ---------- */
  listMembers(projectId: number) {
    return this.request<PagedResult<ProjectMember>>('GET', `/api/projects/${projectId}/members`);
  }
  addMember(projectId: number, body: { user_id?: number; username?: string; role?: string }) {
    return this.request<ProjectMember>('POST', `/api/projects/${projectId}/members`, body);
  }
  removeMember(projectId: number, userId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/projects/${projectId}/members/${userId}`);
  }
  updateMemberRole(projectId: number, userId: number, role: string) {
    return this.request<ProjectMember>('PATCH', `/api/projects/${projectId}/members/${userId}`, { role });
  }

  /* ---------- Notifications ---------- */
  listNotifications(params?: { limit?: number; offset?: number; unread_only?: boolean }) {
    return this.request<PagedResult<Notification>>('GET', '/api/notifications', undefined, params as Record<string, string | number | undefined>);
  }
  getUnreadCount() {
    return this.request<{ count: number }>('GET', '/api/notifications/unread-count');
  }
  markRead(notifId: number) {
    return this.request<Notification>('POST', `/api/notifications/${notifId}/read`);
  }
  markAllRead() {
    return this.request<{ ok: boolean; count: number }>('POST', '/api/notifications/read-all');
  }
  deleteNotification(notifId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/notifications/${notifId}`);
  }

  /* ---------- Project Stats ---------- */
  // Epic 21 Story 21.2: 添加缓存支持，可配置 TTL
  private readonly STATS_CACHE_TTL = parseInt(window.AGENTBOARD_STATS_CACHE_TTL || '30000', 10); // 默认 30 秒

  getProjectStats(projectId: number) {
    const cacheKey = `/api/projects/${projectId}/stats`;
    const cached = apiCache.getWithTTL<ProjectStats>(cacheKey, this.STATS_CACHE_TTL);
    if (cached) return of(cached);
    return this.request<ProjectStats>('GET', cacheKey).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }

  // Story 21.2: 写入操作时清除 stats 缓存
  invalidateStatsCache(projectId?: number): void {
    if (projectId) {
      apiCache.invalidate(`/api/projects/${projectId}/stats`);
    } else {
      apiCache.invalidatePrefix('/api/projects');
    }
  }

  /* ---------- Admin ---------- */
  adminListUsers(params?: { limit?: number; offset?: number }) {
    return this.request<PagedResult<any>>('GET', '/api/admin/users', undefined, params as Record<string, string | number | undefined>);
  }
  adminUpdateUser(userId: number, isAdmin: boolean) {
    return this.request<any>('PATCH', `/api/admin/users/${userId}`, { is_admin: isAdmin });
  }
  adminListProjects(params?: { limit?: number; offset?: number }) {
    return this.request<PagedResult<any>>('GET', '/api/admin/projects', undefined, params as Record<string, string | number | undefined>);
  }
  adminDeleteProject(projectId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/admin/projects/${projectId}`);
  }

  /* ---------- Attachment ---------- */
  listAttachments(taskId: number) {
    return this.request<Attachment[]>('GET', `/api/tasks/${taskId}/attachments`);
  }
  getAttachmentInfo(attachmentId: number) {
    return this.request<Attachment>('GET', `/api/attachments/${attachmentId}/info`);
  }
  uploadAttachment(taskId: number, file: File) {
    const formData = new FormData();
    formData.append('file', file);
    const token = localStorage.getItem('agentboard_token');
    const headers = token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : undefined;
    return this.http.request<Attachment>('POST', `${this.baseUrl}/api/tasks/${taskId}/attachments`, {
      body: formData,
      headers,
    }).pipe(catchError((error: HttpErrorResponse) => {
      const payload = error.error as ApiErrorBody | undefined;
      const detail = Array.isArray(payload?.detail)
        ? payload?.detail.map((item) => item.msg || '参数错误').join('；')
        : payload?.detail;
      return throwError(() => new Error(detail || error.message || `HTTP ${error.status}`));
    }));
  }
  deleteAttachment(attachmentId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/attachments/${attachmentId}`);
  }
  getAttachmentUrl(attachmentId: number): string {
    const token = localStorage.getItem('agentboard_token') || '';
    return `${this.baseUrl}/api/attachments/${attachmentId}?token=${encodeURIComponent(token)}`;
  }

  /* ---------- Agent Schedules ---------- */
  listSchedules(projectId: number) {
    return this.request<AgentSchedule[]>('GET', `/api/projects/${projectId}/schedules`);
  }
  createSchedule(projectId: number, body: { title: string; schedule_type: string; cron_expr?: string }) {
    return this.request<AgentSchedule>('POST', `/api/projects/${projectId}/schedules`, body);
  }
  updateSchedule(scheduleId: number, body: Partial<AgentSchedule>) {
    return this.request<AgentSchedule>('PATCH', `/api/schedules/${scheduleId}`, body);
  }
  deleteSchedule(scheduleId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/schedules/${scheduleId}`);
  }

  /* ---------- Agent Runs ---------- */
  listRuns(scheduleId: number) {
    return this.request<AgentRun[]>('GET', `/api/schedules/${scheduleId}/runs`);
  }
  retryRun(scheduleId: number, taskId: number) {
    return this.request<AgentRun>('POST', `/api/schedules/${scheduleId}/runs`, { task_id: taskId });
  }

  /* ---------- Bulk Operations ---------- */
  // Epic 21 Story 21.2: 写入操作时清除相关缓存
  bulkUpdateTasks(taskIds: number[], updates: { status?: string; priority?: string; sprint_id?: number }) {
    return this.request<{ updated: any[]; errors: any[] }>('POST', '/api/tasks/bulk-update', { task_ids: taskIds, ...updates }).pipe(
      tap(() => {
        apiCache.invalidatePrefix('/api/stories');
        apiCache.invalidatePrefix('/api/projects'); // 清除项目统计缓存
      })
    );
  }

  bulkDeleteTasks(taskIds: number[]) {
    return this.request<{ deleted: any[]; errors: any[] }>('POST', '/api/tasks/bulk-delete', { task_ids: taskIds }).pipe(
      tap(() => {
        apiCache.invalidatePrefix('/api/stories');
        apiCache.invalidatePrefix('/api/projects'); // 清除项目统计缓存
      })
    );
  }

  /* ---------- Epic 22: Task Dependencies ---------- */
  getTaskDependencies(taskId: number) {
    return this.request<TaskDependencies>('GET', `/api/tasks/${taskId}/dependencies`);
  }
  addTaskDependency(taskId: number, dependsOnId: number, dependencyType: string = 'blocks') {
    return this.request<any>('POST', `/api/tasks/${taskId}/dependencies`, undefined, { depends_on_id: dependsOnId, dependency_type: dependencyType });
  }
  removeTaskDependency(dependencyId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/dependencies/${dependencyId}`);
  }

  /* ---------- Epic 22: Import/Export ---------- */
  exportProject(projectId: number) {
    return this.request<any>('GET', `/api/projects/${projectId}/export`);
  }
  importTasks(projectId: number, tasksData: any[]) {
    return this.request<{ imported: any[]; errors: any[] }>('POST', `/api/projects/${projectId}/import`, { tasks: tasksData });
  }

  /* ---------- Epic 22: Webhooks ---------- */
  listWebhooks(projectId?: number) {
    return this.request<PagedResult<WebhookConfig>>('GET', '/api/webhooks', undefined, projectId !== undefined ? { project_id: projectId } : undefined);
  }
  createWebhook(projectId: number | undefined, body: { name: string; url: string; secret?: string; events?: string[] }) {
    return this.request<WebhookConfig>('POST', '/api/webhooks', body, projectId !== undefined ? { project_id: projectId } : undefined);
  }
  deleteWebhook(webhookId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/webhooks/${webhookId}`);
  }
  toggleWebhook(webhookId: number, enabled: boolean) {
    return this.request<WebhookConfig>('PATCH', `/api/webhooks/${webhookId}`, undefined, { enabled: enabled ? 1 : 0 });
  }

  /* ---------- Epic 22: Audit Logs ---------- */
  listAuditLogs(params?: { entity_type?: string; entity_id?: number; user_id?: number; action?: string; limit?: number; offset?: number }) {
    return this.request<PagedResult<AuditLog>>('GET', '/api/audit-logs', undefined, params as Record<string, string | number | undefined>);
  }
}
