import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';

import { ApiErrorBody, Attachment, AuthResult, Comment, Epic, Notification, PagedResult, Project, ProjectMember, ProjectStats, Sprint, Story, Task, AgentSchedule, AgentRun } from './models';

declare global {
  interface Window {
    AGENTBOARD_API?: string;
  }
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  readonly baseUrl = window.AGENTBOARD_API || 'http://127.0.0.1:8000';

  constructor(private readonly http: HttpClient) {}

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
  ): Observable<T> {
    return this.http
      .request<T>(method, `${this.baseUrl}${path}`, { ...this.options(params), body })
      .pipe(
        catchError((error: HttpErrorResponse) => {
          const payload = error.error as ApiErrorBody | undefined;
          const detail = Array.isArray(payload?.detail)
            ? payload?.detail.map((item) => item.msg || '参数错误').join('；')
            : payload?.detail;
          return throwError(() => new Error(detail || error.message || `HTTP ${error.status}`));
        }),
      );
  }

  listProjects() {
    return this.request<PagedResult<Project>>('GET', '/api/projects');
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
    return this.request<Project>('POST', '/api/projects', body);
  }
  updateProject(id: number, body: Partial<Project>) {
    return this.request<Project>('PATCH', `/api/projects/${id}`, body);
  }
  deleteProject(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/projects/${id}`);
  }

  listEpics(projectId: number) {
    return this.request<Epic[]>('GET', `/api/projects/${projectId}/epics`);
  }
  getEpic(id: number) {
    return this.request<Epic>('GET', `/api/epics/${id}`);
  }
  createEpic(projectId: number, body: { title: string; description?: string }) {
    return this.request<Epic>('POST', `/api/projects/${projectId}/epics`, body);
  }
  updateEpic(id: number, body: Partial<Epic>) {
    return this.request<Epic>('PATCH', `/api/epics/${id}`, body);
  }
  deleteEpic(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/epics/${id}`);
  }

  listStories(epicId: number) {
    return this.request<Story[]>('GET', `/api/epics/${epicId}/stories`);
  }
  getStory(id: number) {
    return this.request<Story>('GET', `/api/stories/${id}`);
  }
  createStory(epicId: number, body: { title: string; description?: string }) {
    return this.request<Story>('POST', `/api/epics/${epicId}/stories`, body);
  }
  updateStory(id: number, body: Partial<Story>) {
    return this.request<Story>('PATCH', `/api/stories/${id}`, body);
  }
  deleteStory(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/stories/${id}`);
  }

  listTasks(storyId: number) {
    return this.request<Task[]>('GET', `/api/stories/${storyId}/tasks`);
  }
  searchTasks(params: Record<string, string | number | undefined>) {
    return this.request<Task[]>('GET', '/api/tasks', undefined, params);
  }
  getTask(id: number) {
    return this.request<Task>('GET', `/api/tasks/${id}`);
  }
  createTask(storyId: number, body: Partial<Task> & { project_id: number; title: string }) {
    return this.request<Task>('POST', `/api/stories/${storyId}/tasks`, body);
  }
  updateTask(id: number, body: Partial<Task>) {
    return this.request<Task>('PATCH', `/api/tasks/${id}`, body);
  }
  setTaskStatus(id: number, status: string) {
    return this.request<Task>('PUT', `/api/tasks/${id}/status`, { status });
  }
  deleteTask(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/tasks/${id}`);
  }
  generateSubtasks(id: number) {
    return this.request<Task[]>('POST', `/api/tasks/${id}/generate-subtasks`);
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
    return this.request<Sprint[]>('GET', `/api/projects/${projectId}/sprints`);
  }
  getSprint(id: number) {
    return this.request<Sprint>('GET', `/api/sprints/${id}`);
  }
  createSprint(projectId: number, body: { title: string; goal?: string }) {
    return this.request<Sprint>('POST', `/api/projects/${projectId}/sprints`, body);
  }
  updateSprint(id: number, body: Partial<Sprint>) {
    return this.request<Sprint>('PATCH', `/api/sprints/${id}`, body);
  }
  activateSprint(id: number) {
    return this.request<Sprint>('POST', `/api/sprints/${id}/activate`);
  }
  completeSprint(id: number) {
    return this.request<Sprint>('POST', `/api/sprints/${id}/complete`);
  }
  deleteSprint(id: number) {
    return this.request<{ ok: boolean }>('DELETE', `/api/sprints/${id}`);
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
  getProjectStats(projectId: number) {
    return this.request<ProjectStats>('GET', `/api/projects/${projectId}/stats`);
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
}
