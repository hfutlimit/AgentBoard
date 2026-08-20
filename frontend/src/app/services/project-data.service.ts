import { Injectable, signal } from '@angular/core';
import type { Project, ProjectMember, Epic, ProjectTabKind } from '../models';

/**
 * ProjectDataService — Epic 152 / Story 332 (Sub-PR 1) 路由完全收口 shim 阶段
 *
 * 为 8 tab children 组件（路由化后由 Router 渲染，接收不到 @Input）提供共享数据源。
 * app.ts loadRoute() 加载数据后写入 service；8 tab inject service 拿数据。
 *
 * Sub-PR 1 阶段：仅供 ProjectWorkspaceShell + 8 tab children 备用（仍走 @Input）。
 * Sub-PR 1b 阶段：app.ts loadRoute() 写 service；8 tab 改 inject service 拿数据。
 * Story 333 阶段：service 升级为 ProjectContextService + ActivatedRoute 配合。
 *
 * 设计原则：
 * - signal-based 与现有 app.ts 风格一致
 * - setXxx() 集中管理 project context
 * - clear() 用于路由变化时重置（避免项目 A 数据泄漏到项目 B）
 */
@Injectable({ providedIn: 'root' })
export class ProjectDataService {
  readonly project = signal<Project | null>(null);
  readonly projectId = signal<number | null>(null);
  readonly activeTab = signal<ProjectTabKind>('overview');
  readonly members = signal<ProjectMember[]>([]);
  readonly epics = signal<Epic[]>([]);

  // Sub-PR 1b 阶段：以下 8 tab 共享数据按需加 signal + setter
  // - backlog tasks / kanban columns / proposals / documents / settings etc.

  setProject(project: Project | null): void {
    this.project.set(project);
    this.projectId.set(project?.id ?? null);
  }

  setMembers(members: ProjectMember[]): void {
    this.members.set(members);
  }

  setEpics(epics: Epic[]): void {
    this.epics.set(epics);
  }

  setActiveTab(tab: ProjectTabKind): void {
    this.activeTab.set(tab);
  }

  /** 路由变化 / 登出时清空（避免项目 A 数据泄漏到项目 B） */
  clear(): void {
    this.project.set(null);
    this.projectId.set(null);
    this.members.set([]);
    this.epics.set([]);
    this.activeTab.set('overview');
  }
}
