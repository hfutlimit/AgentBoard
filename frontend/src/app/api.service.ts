import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, of, throwError, timer, Subject } from 'rxjs';
import { catchError, tap, map, switchMap, mergeMap, debounceTime, takeUntil } from 'rxjs/operators';

import { ApiErrorBody, ApiKeyInfo, Attachment, AuthResult, Comment, Epic, KanbanBoard, Notification, OverviewStats, PagedResult, Project, ProjectMember, ProjectStats, ReviewStats, ReviewTimeoutResult, Sprint, Story, Task, AgentSchedule, AgentRun, TaskDependencies, AuditLog, UserProfile, WebhookConfig, DocumentItem, DocumentCommentItem, DocumentFolder, DocumentType, DocumentStatus, ProposalItem, ProposalRoundItem, ProposalQuestionItem, ProposalStatus, TicketRequestItem, TicketType, AgentRow, StoryStatusHistoryRow } from './models';

export const AUTH_EXPIRED_EVENT = 'agentboard:auth-expired';

declare global {
  interface Window {
    AGENTBOARD_API?: string;
    AGENTBOARD_STATS_CACHE_TTL?: string;
  }
}

// Task 261: resolve the API base URL for both production and local dev.
// Production: web_app.py / scripts/deploy/configure-api-url.ps1 inject the real
// URL into window.AGENTBOARD_API (replacing the "__API_URL__" placeholder in
// index.html). In local dev (ng serve) that placeholder is left as-is, so we
// treat it as unset and use a relative base URL — letting proxy.conf.json
// forward /api to the local backend.
// Non-localhost without an injected URL: fall back to the same-origin relative
// path (''), which works with the IIS/ARR reverse-proxy topology (/api/* is
// proxied to the local WebAPI). The historical hardcoded 'http://127.0.0.1:8000'
// pointed at the *browser's own machine* and broke every API call whenever the
// injected value was empty or missing (2026-08-09 prod outage).
export function resolveApiBase(): string {
  const injected = (window as any).AGENTBOARD_API as string | undefined;
  if (injected && injected !== '__API_URL__') return injected;
  return '';
}

// ========== Debounce Helper for Task 705 ==========
class RequestDebouncer {
  private pending = new Map<string, Subject<() => void>>();
  private readonly DEFAULT_DEBOUNCE_MS = 300; // Default debounce time

  /**
   * Debounce a request - only executes the last one within the time window
   * Returns a function that can be called to trigger the debounced request
   */
  debounce<T>(key: string, factory: () => Observable<T>, ms: number = this.DEFAULT_DEBOUNCE_MS): Observable<T> {
    return new Observable<T>(observer => {
      // Cancel any pending request for this key
      if (this.pending.has(key)) {
        const subject = this.pending.get(key)!;
        // Complete will be handled by takeUntil
      }

      const subject = new Subject<() => void>();
      this.pending.set(key, subject);

      const timeoutId = setTimeout(() => {
        this.pending.delete(key);
        const result = factory();
        const subscription = result.subscribe({
          next: (value) => observer.next(value),
          error: (err) => observer.error(err),
          complete: () => observer.complete()
        });
        return () => subscription.unsubscribe();
      }, ms);

      // Cleanup on unsubscribe
      return () => {
        clearTimeout(timeoutId);
        this.pending.delete(key);
        subject.complete();
      };
    });
  }

  /**
   * Check if there's a pending request for this key
   */
  hasPending(key: string): boolean {
    return this.pending.has(key);
  }

  /**
   * Cancel pending request for this key
   */
  cancel(key: string): void {
    this.pending.delete(key);
  }

  /**
   * Clear all pending requests
   */
  clear(): void {
    this.pending.clear();
  }
}

const requestDebouncer = new RequestDebouncer();

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

// ========== Performance Metrics (Task 708) ==========
export interface ApiMetric {
  path: string;
  method: string;
  duration: number;
  status: 'success' | 'error';
  timestamp: number;
}

class PerformanceTracker {
  private metrics: ApiMetric[] = [];
  private readonly MAX_METRICS = 50;

  record(path: string, method: string, duration: number, success: boolean): void {
    this.metrics.push({
      path,
      method,
      duration,
      status: success ? 'success' : 'error',
      timestamp: Date.now(),
    });
    if (this.metrics.length > this.MAX_METRICS) {
      this.metrics.shift();
    }
  }

  getMetrics(): ApiMetric[] {
    return [...this.metrics];
  }

  getAverageDuration(): number {
    if (this.metrics.length === 0) return 0;
    const total = this.metrics.reduce((sum, m) => sum + m.duration, 0);
    return total / this.metrics.length;
  }

  getSuccessRate(): number {
    if (this.metrics.length === 0) return 100;
    const successCount = this.metrics.filter(m => m.status === 'success').length;
    return (successCount / this.metrics.length) * 100;
  }

  getRecentMetrics(count: number = 10): ApiMetric[] {
    return this.metrics.slice(-count);
  }

  clear(): void {
    this.metrics = [];
  }
}

export const perfTracker = new PerformanceTracker();

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
  // Task 261: local dev hot-reload support — resolveApiBase() returns a relative
  // base on the dev server so proxy.conf.json can forward /api to the local
  // backend. Production keeps the injected absolute URL.
  readonly baseUrl = resolveApiBase();
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

  // Task 705: API 响应缓存与防抖 - 防抖请求方法
  // 使用防抖减少快速连续请求，同一 key 的请求只在最后一个生效
  debouncedRequest<T>(
    key: string,
    factory: () => Observable<T>,
    ms: number = 300
  ): Observable<T> {
    // 如果有缓存且未过期，直接返回缓存
    const cached = apiCache.get<T>(key);
    if (cached) {
      return of(cached);
    }
    // 否则使用防抖
    return requestDebouncer.debounce(key, () => {
      return this.request<T>('GET', key).pipe(
        tap(data => apiCache.set(key, data))
      );
    }, ms);
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
    // Task 708: Performance tracking - record start time
    const startTime = performance.now();

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
        tap({
          next: () => {
            // Task 708: Record successful request duration
            const duration = performance.now() - startTime;
            perfTracker.record(path, method, duration, true);
          },
          error: (error: HttpErrorResponse) => {
            // Task 708: Record failed request duration
            const duration = performance.now() - startTime;
            perfTracker.record(path, method, duration, false);
          }
        }),
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
          // 保留 HTTP 状态码，便于调用方做权限错误自动跳转等场景
          const wrapped: Error & { status?: number } = new Error(
            detail || error.message || `HTTP ${error.status}`,
          );
          wrapped.status = error.status;
          return throwError(() => wrapped);
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

  listMyProjects(role?: 'owner' | 'member') {
    return this.request<PagedResult<Project>>('GET', '/api/users/me/projects', undefined, role ? { role } : undefined);
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
  createStory(epicId: number, body: { title: string; description?: string; needs_design?: boolean }) {
    return this.request<Story>('POST', `/api/epics/${epicId}/stories`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/epics'))
    );
  }
  updateStory(id: number, body: Partial<Story>) {
    return this.request<Story>('PATCH', `/api/stories/${id}`, body);
  }
  // Ticket 全流程（2026-08-09）：用户确认 Story 开始（人工闸门）+ 自动收尾 + 状态历史
  confirmStory(id: number) {
    return this.request<Story>('POST', `/api/stories/${id}/confirm`);
  }
  completeStory(id: number) {
    return this.request<Story>('POST', `/api/stories/${id}/complete`);
  }
  storyStatusHistory(id: number) {
    return this.request<{ items: StoryStatusHistoryRow[]; total: number }>(
      'GET', `/api/stories/${id}/status-history`);
  }
  listAgents() {
    return this.request<AgentRow[]>('GET', '/api/agents');
  }
  registerAgent(body: Partial<AgentRow>) {
    return this.request<AgentRow>('POST', '/api/agents/register', body);
  }
  updateAgent(agentId: string, body: Partial<AgentRow>) {
    return this.request<AgentRow>('PUT', `/api/agents/${agentId}`, body);
  }
  deleteAgent(agentId: string) {
    return this.request<{ ok: boolean }>('DELETE', `/api/agents/${agentId}`);
  }
  probeAgent(agentId: string) {
    return this.request<AgentRow>('POST', `/api/agents/${agentId}/probe`, {});
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
    return this.request<{ items: Task[]; total: number }>('GET', cacheKey).pipe(
      tap(data => apiCache.set(cacheKey, data.items)),
      map(data => data.items)
    );
  }

  /** 分页加载 Story 任务，返回 { items, total } */
  listTasksPaginated(storyId: number, limit: number, offset: number) {
    const cacheKey = `/api/stories/${storyId}/tasks?limit=${limit}&offset=${offset}`;
    return this.request<{ items: Task[]; total: number }>('GET', `/api/stories/${storyId}/tasks`, undefined, { limit, offset });
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
  /** Epic 70 v5.7: 全局 Story 关键词搜索 */
  searchStories(params: { q: string; limit?: number }) {
    const cacheKey = `/api/search/stories?q=${params.q}&limit=${params.limit ?? 20}`;
    const cached = apiCache.getWithTTL<any[]>(cacheKey, 30000);
    if (cached) return of(cached);
    return this.request<any[]>('GET', '/api/search/stories', undefined, params).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  /** Epic 119 v6.13: 全局 Epic 关键词搜索（命令面板补齐实体搜索结果） */
  searchEpics(params: { q: string; limit?: number }) {
    const cacheKey = `/api/search/epics?q=${params.q}&limit=${params.limit ?? 20}`;
    const cached = apiCache.getWithTTL<any[]>(cacheKey, 30000);
    if (cached) return of(cached);
    return this.request<any[]>('GET', '/api/search/epics', undefined, params).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  /** Epic 120 v6.14: 全局 Sprint 关键词搜索（命令面板补齐第 6 类实体） */
  searchSprints(params: { q: string; limit?: number }) {
    const cacheKey = `/api/search/sprints?q=${params.q}&limit=${params.limit ?? 20}`;
    const cached = apiCache.getWithTTL<any[]>(cacheKey, 30000);
    if (cached) return of(cached);
    return this.request<any[]>('GET', '/api/search/sprints', undefined, params).pipe(
      tap(data => apiCache.set(cacheKey, data))
    );
  }
  /** Epic 121 v6.15: 当前用户通知关键词搜索（命令面板补齐第 7 类实体；后端按 user_id 隔离） */
  searchNotifications(params: { q: string; limit?: number }) {
    return this.request<Notification[]>('GET', '/api/search/notifications', undefined, params);
  }
  /** Epic 131 v6.16: 全局 Agent 关键词搜索（命令面板补齐第 8 类实体；后端仅返回 enabled） */
  searchAgents(params: { q: string; limit?: number }) {
    return this.request<AgentRow[]>('GET', '/api/search/agents', undefined, params);
  }
  /** Epic 132 v6.17: 全局 Proposal 关键词搜索（命令面板补齐第 9 类实体；后端按可见项目收敛） */
  searchProposals(params: { q: string; limit?: number }) {
    return this.request<ProposalItem[]>('GET', '/api/search/proposals', undefined, params);
  }
  /** Epic 133 v6.18: 全局 Ticket 关键词搜索（命令面板补齐第 10 类实体；后端按提案可见项目收敛） */
  searchTicketRequests(params: { q: string; limit?: number }) {
    return this.request<TicketRequestItem[]>('GET', '/api/search/tickets', undefined, params);
  }
  /** Epic 134 v6.19: 全局定时计划关键词搜索（命令面板补齐第 11 类实体；后端按成员项目收敛） */
  searchSchedules(params: { q: string; limit?: number }) {
    return this.request<AgentSchedule[]>('GET', '/api/search/schedules', undefined, params);
  }
  /** Epic 135 v6.20: 全局执行记录关键词搜索（命令面板补齐第 12 类实体；后端按成员项目收敛） */
  searchRuns(params: { q: string; limit?: number }) {
    return this.request<AgentRun[]>('GET', '/api/search/runs', undefined, params);
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
  setTaskStatus(id: number, status: string, statusReason?: string) {
    // Story 265：done/blocked 必填 status_reason；其他状态忽略
    const body: { status: string; status_reason?: string } = { status };
    if (statusReason) body.status_reason = statusReason;
    return this.request<Task>('PUT', `/api/tasks/${id}/status`, body).pipe(
      // A-22: 状态变更后使任务列表缓存失效，否则列表/看板仍显示旧状态（快速完成二次点击失效的根因）
      tap(() => apiCache.invalidatePrefix('/api/stories')),
    );
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
  listStoryComments(storyId: number) {
    return this.request<Comment[]>('GET', `/api/stories/${storyId}/comments`);
  }
  addStoryComment(storyId: number, body: { author: string; content: string }) {
    return this.request<Comment>('POST', `/api/stories/${storyId}/comments`, body);
  }
  listEpicComments(epicId: number) {
    return this.request<Comment[]>('GET', `/api/epics/${epicId}/comments`);
  }
  addEpicComment(epicId: number, body: { author: string; content: string }) {
    return this.request<Comment>('POST', `/api/epics/${epicId}/comments`, body);
  }

  register(username: string, password: string) {
    return this.request<AuthResult>('POST', '/api/auth/register', { username, password });
  }
  login(username: string, password: string) {
    return this.request<AuthResult>('POST', '/api/auth/login', { username, password });
  }

  me() {
    return this.request<UserProfile>('GET', '/api/auth/me');
  }
  updateProfile(body: { display_name: string; email: string; avatar_url: string }) {
    return this.request<UserProfile>('PATCH', '/api/auth/me', body);
  }
  changePassword(body: { current_password: string; new_password: string }) {
    return this.request<void>('POST', '/api/auth/change-password', body);
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

  /* ---------- Review Stats (Epic 122 S3/S4) ---------- */
  // 项目级评审统计运营视图 + 超时重派（S3 M2 后端已交付，S4 前端接入）
  getReviewStats(projectId: number, days = 7) {
    return this.request<ReviewStats>('GET', '/api/review-stats', undefined, { project_id: projectId, days });
  }

  reassignReviewTimeout(projectId: number | undefined, body: { timeout_minutes?: number; max_per_run?: number }) {
    return this.request<ReviewTimeoutResult>('POST', '/api/review-stats/reassign-timeout', body, projectId ? { project_id: projectId } : undefined);
  }

  /* ---------- Dashboard Overview (Epic 117 / Task 995) ---------- */
  // 首页单请求聚合统计；短 TTL 缓存（默认 15s），写入操作时随 stats 一起失效
  private readonly OVERVIEW_CACHE_TTL = 15000;

  getOverview() {
    const cacheKey = '/api/overview';
    const cached = apiCache.getWithTTL<OverviewStats>(cacheKey, this.OVERVIEW_CACHE_TTL);
    if (cached) return of(cached);
    return this.request<OverviewStats>('GET', cacheKey).pipe(
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
  createSchedule(projectId: number, body: { title: string; schedule_type: string; cron_expr?: string; agent?: string }) {
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
  bulkUpdateTasks(taskIds: number[], updates: { status?: string; priority?: string; sprint_id?: number; assignee_id?: number; clear_assignee?: boolean; due_date?: string | null; clear_due_date?: boolean }) {
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

  /* ---------- Epic 25: API Keys ---------- */
  listApiKeys() {
    return this.request<{ items: ApiKeyInfo[] }>('GET', '/api/api-keys');
  }
  createApiKey(body: { name: string; permissions: string[] }) {
    return this.request<ApiKeyInfo & { key: string }>('POST', '/api/api-keys', body);
  }
  revokeApiKey(keyId: number) {
    return this.request<void>('DELETE', `/api/api-keys/${keyId}`);
  }

  /* ---------- Epic 15: 项目文档维护 ---------- */
  // HttpClient PATCH 在 AgentBoard 中不会 emit（已知缺陷），文档更新统一用 fetch 绕过
  private patchJson<T>(path: string, body: unknown): Observable<T> {
    const apiUrl = resolveApiBase();
    const token = localStorage.getItem('agentboard_token');
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return new Observable<T>((observer) => {
      fetch(`${apiUrl}${path}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify(body),
      })
        .then(async (r) => {
          if (!r.ok) {
            let detail = r.statusText;
            try {
              const payload = await r.json();
              detail = Array.isArray(payload?.detail)
                ? payload.detail.map((i: any) => i.msg || '参数错误').join('；')
                : (payload?.detail || detail);
            } catch { /* ignore */ }
            throw new Error(detail);
          }
          const data = (r.status === 204 || r.headers.get('content-length') === '0')
            ? (undefined as unknown as T)
            : ((await r.json()) as T);
          observer.next(data);
          observer.complete();
        })
        .catch((err) => observer.error(err));
    });
  }

  listDocuments(params?: { project_id?: number; epic_id?: number; story_id?: number; type?: DocumentType; status?: DocumentStatus; q?: string }) {
    return this.request<DocumentItem[]>('GET', '/api/documents', undefined, params as Record<string, string | number | undefined> | undefined);
  }

  /* ---------- Kanban (Epic 130: 项目看板) ---------- */
  getProjectKanban(projectId: number, includeAll = false) {
    return this.request<KanbanBoard>('GET', `/api/projects/${projectId}/kanban`, undefined,
      includeAll ? { include_all: 'true' } : undefined);
  }

  /* ---------- Document folders (Epic 15 增强：文件夹 / 子文件夹) ---------- */
  listDocumentFolders(params?: { project_id?: number }) {
    return this.request<DocumentFolder[]>('GET', '/api/document-folders', undefined, params as Record<string, string | number | undefined> | undefined);
  }
  createDocumentFolder(body: { project_id: number; name: string; parent_id?: number | null }) {
    return this.request<DocumentFolder>('POST', '/api/document-folders', body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/document-folders'))
    );
  }
  updateDocumentFolder(id: number, body: { name?: string; parent_id?: number | null }) {
    return this.patchJson<DocumentFolder>(`/api/document-folders/${id}`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/document-folders'))
    );
  }
  deleteDocumentFolder(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/document-folders/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/document-folders'))
    );
  }
  getDocument(id: number) {
    return this.request<DocumentItem>('GET', `/api/documents/${id}`);
  }
  createDocument(body: { project_id: number; title: string; content?: string; type?: DocumentType; status?: DocumentStatus; epic_id?: number | null; story_id?: number | null; folder_id?: number | null }) {
    return this.request<DocumentItem>('POST', '/api/documents', body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/documents'))
    );
  }
  updateDocument(id: number, body: Partial<DocumentItem>) {
    return this.patchJson<DocumentItem>(`/api/documents/${id}`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/documents'))
    );
  }
  setDocumentStatus(id: number, status: DocumentStatus) {
    return this.request<DocumentItem>('PUT', `/api/documents/${id}/status`, { status }).pipe(
      tap(() => apiCache.invalidatePrefix('/api/documents'))
    );
  }
  deleteDocument(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/documents/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/documents'))
    );
  }
  listDocumentComments(id: number) {
    return this.request<DocumentCommentItem[]>('GET', `/api/documents/${id}/comments`);
  }
  addDocumentComment(id: number, body: { author: string; content: string }) {
    return this.request<DocumentCommentItem>('POST', `/api/documents/${id}/comments`, body);
  }
  updateDocumentComment(commentId: number, body: { content: string }) {
    return this.patchJson<DocumentCommentItem>(`/api/document-comments/${commentId}`, body);
  }
  deleteDocumentComment(commentId: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/document-comments/${commentId}`);
  }

  /* ---------- Epic 96 P0: Proposal 澄清回路（问答工作台） ---------- */
  listProposals(params?: { project_id?: number; status?: ProposalStatus | ''; q?: string; limit?: number }) {
    return this.request<ProposalItem[]>(
      'GET', '/api/proposals', undefined,
      params as Record<string, string | number | undefined> | undefined,
    );
  }
  getProposal(id: number) {
    return this.request<ProposalItem>('GET', `/api/proposals/${id}`);
  }
  createProposal(body: { project_id: number; title: string; content?: string }) {
    return this.request<ProposalItem>('POST', '/api/proposals', body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/proposals'))
    );
  }
  updateProposal(id: number, body: { title?: string; content?: string; converged_spec?: string }) {
    return this.patchJson<ProposalItem>(`/api/proposals/${id}`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/proposals'))
    );
  }
  setProposalStatus(id: number, status: ProposalStatus, error?: string) {
    return this.request<ProposalItem>('PUT', `/api/proposals/${id}/status`, { status, error }).pipe(
      tap(() => apiCache.invalidatePrefix('/api/proposals'))
    );
  }
  deleteProposal(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/proposals/${id}`).pipe(
      tap(() => apiCache.invalidatePrefix('/api/proposals'))
    );
  }
  listProposalRounds(id: number) {
    return this.request<ProposalRoundItem[]>('GET', `/api/proposals/${id}/rounds`);
  }
  answerProposalQuestion(qid: number, body: { answer?: string; unsure?: boolean }) {
    return this.request<ProposalQuestionItem>('PUT', `/api/proposal-questions/${qid}/answer`, body).pipe(
      tap(() => apiCache.invalidatePrefix('/api/proposals'))
    );
  }

  /* ---------- Proposal → Ticket 异步转化（2026-08-08 文档 #59）---------- */
  listTicketRequests(proposalId: number) {
    return this.request<TicketRequestItem[]>(
      'GET', `/api/proposals/${proposalId}/ticket-requests`,
    );
  }
  createTicketRequest(proposalId: number, body: { type: TicketType; epic_id?: number; story_id?: number; title?: string }) {
    return this.request<TicketRequestItem>(
      'POST', `/api/proposals/${proposalId}/ticket-requests`, body,
    ).pipe(
      tap(() => apiCache.invalidatePrefix(`/api/proposals/${proposalId}`)),
      tap(() => apiCache.invalidatePrefix('/api/proposals')),
    );
  }
}
