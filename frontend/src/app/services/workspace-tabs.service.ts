import { Injectable, computed, signal } from '@angular/core';

/**
 * WorkspaceTabsService — 项目工作台多 Tab 状态管理（2026-08-21 结构调整）
 *
 * 设计要点：
 * - 8 个子视图（概览/看板/Epics/工作项/提案/文档/成员/设置）可同时作为独立 tab 挂载
 * - 同 (projectId, kind) 至多 1 个 tab（语义上 8 个菜单项互斥）
 * - 切换项目 → reset 所有 tab（用户回答"切项目清空"）
 * - in-memory only，不持久化（用户回答"刷新清空"）
 * - Tab 组件始终在 DOM 里，非激活的用 CSS 隐藏，状态由 Angular 保留
 */

export type WorkspaceTabKind =
  | 'overview'
  | 'kanban'
  | 'epics'
  | 'backlog'
  | 'proposals'
  | 'documents'
  | 'members'
  | 'settings';

export interface WorkspaceTab {
  /** 稳定 id: `${projectId}-${kind}` */
  id: string;
  projectId: number;
  kind: WorkspaceTabKind;
  /** 显示文字 */
  title: string;
  /** SVG icon id (与 app.html 内联 SVG <use href="#i-xxx"> 一致) */
  iconId: string;
}

interface TabKindMeta {
  title: string;
  iconId: string;
}

const TAB_META: Record<WorkspaceTabKind, TabKindMeta> = {
  overview:   { title: '概览',     iconId: 'i-home' },
  kanban:     { title: '看板',     iconId: 'i-board' },
  epics:      { title: 'Epics',   iconId: 'i-flag' },
  backlog:    { title: '工作项',   iconId: 'i-list' },
  proposals:  { title: '提案',     iconId: 'i-message' },
  documents:  { title: '文档',     iconId: 'i-file' },
  members:    { title: '成员与 Agents', iconId: 'i-users' },
  settings:   { title: '设置',     iconId: 'i-settings' },
};

@Injectable({ providedIn: 'root' })
export class WorkspaceTabsService {
  private readonly _tabs = signal<WorkspaceTab[]>([]);
  private readonly _activeId = signal<string | null>(null);
  private _currentProjectId: number | null = null;

  /** 只读 tabs 列表（按打开顺序） */
  readonly tabs = this._tabs.asReadonly();
  /** 当前激活 tab 的 id */
  readonly activeId = this._activeId.asReadonly();
  /** 当前激活 tab 对象（若无则 null） */
  readonly activeTab = computed<WorkspaceTab | null>(() => {
    const id = this._activeId();
    if (!id) return null;
    return this._tabs().find((t) => t.id === id) ?? null;
  });
  /** 当前所有 tab 是否为空 */
  readonly isEmpty = computed(() => this._tabs().length === 0);

  /**
   * 打开 / 激活一个 tab。
   * - 若已存在 → 激活即可
   * - 若不存在 → 新建并激活
   */
  openTab(projectId: number, kind: WorkspaceTabKind): void {
    if (this._currentProjectId !== projectId) {
      this.setProject(projectId);
    }
    const id = this.makeId(projectId, kind);
    const existing = this._tabs().find((t) => t.id === id);
    if (existing) {
      this._activeId.set(id);
      return;
    }
    const meta = TAB_META[kind];
    const tab: WorkspaceTab = {
      id,
      projectId,
      kind,
      title: meta.title,
      iconId: meta.iconId,
    };
    this._tabs.update((list) => [...list, tab]);
    this._activeId.set(id);
  }

  /** 关闭一个 tab。激活其相邻 tab（左侧优先，无则右侧，无则 null） */
  closeTab(id: string): void {
    const list = this._tabs();
    const idx = list.findIndex((t) => t.id === id);
    if (idx < 0) return;
    const next = [...list.slice(0, idx), ...list.slice(idx + 1)];
    this._tabs.set(next);
    if (this._activeId() === id) {
      const fallback = next[idx] ?? next[idx - 1] ?? null;
      this._activeId.set(fallback ? fallback.id : null);
    }
  }

  /** 激活已打开的 tab（若不存在则不做事）。返回是否成功 */
  activateTab(id: string): boolean {
    if (!this._tabs().some((t) => t.id === id)) return false;
    this._activeId.set(id);
    return true;
  }

  /**
   * 切换项目 → 清空所有 tab。
   * 调用方应在导航进入新项目时调用。
   */
  setProject(projectId: number): void {
    if (this._currentProjectId === projectId) return;
    this._currentProjectId = projectId;
    this._tabs.set([]);
    this._activeId.set(null);
  }

  /** 关闭除指定外的所有 tab（菜单 "关闭其他"） */
  closeOthers(id: string): void {
    const keep = this._tabs().find((t) => t.id === id);
    if (!keep) return;
    this._tabs.set([keep]);
    this._activeId.set(id);
  }

  /** 关闭所有 tab（激活态清空） */
  closeAll(): void {
    this._tabs.set([]);
    this._activeId.set(null);
  }

  /** 由 (projectId, kind) 推算 id — 暴露给模板 / 路由同步使用 */
  makeId(projectId: number, kind: WorkspaceTabKind): string {
    return `${projectId}-${kind}`;
  }

  /** 工具方法：根据 kind 拿到展示元信息 */
  meta(kind: WorkspaceTabKind): TabKindMeta {
    return TAB_META[kind];
  }
}
