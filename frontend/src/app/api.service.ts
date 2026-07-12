import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';

import { ApiErrorBody, AuthResult, Comment, Epic, Project, Story, Task } from './models';

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
    return this.request<Project[]>('GET', '/api/projects');
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
}
