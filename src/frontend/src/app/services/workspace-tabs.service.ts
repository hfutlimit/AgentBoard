import { Injectable, computed, signal } from '@angular/core';

/**
 * WorkspaceTabsService — 项目工作台多 Tab 状态管理（2026-08-21 结构调整）
 *
 * 设计要点：
 * - 8 个子视图（概览/看板/Epics/工作项/提案/文档/成员/设置）可同时作为独立 tab 挂载
 * - 同 (projectId, kind) 至多 1 个 tab（语义上 8 个菜单项互斥）
 * - 切换项目 → reset 所有 tab（用户回答"切项目清空"）
 * - Tab 组件始终在 DOM 里，非激活的用 CSS 隐藏，状态由 Angular 保留
 *
 * 2026-09-08 Story #435：Tab 从「页面」语义升级为「工作上下文」语义
 * - level / accent：让 Tab 条能按 Project → Epic → Story → Task 分层着色
 * - parentId / parentTitle：子 Tab 聚拢到父 Tab 之后并缩进显示
 * - pinned：固定 Tab 置顶，且不被「关闭其他 / 全部关闭」清掉
 * - 最近访问：记录用户最近打开过的实体，刷新后可恢复工作上下文
 * - Pin 与最近访问均按 projectId 持久化到 localStorage，切项目互不污染
 */

export type WorkspaceTabKind =
  | 'overview'
  | 'kanban'
  | 'epics'
  | 'backlog'
  | 'proposals'
  | 'documents'
  | 'members'
  | 'settings'
  | 'epic'
  | 'proposal'
  | 'story'
  | 'task';

export type WorkspaceSectionTabKind = Exclude<WorkspaceTabKind, 'epic' | 'proposal' | 'story' | 'task'>;
export type WorkspaceEntityTabKind = Extract<WorkspaceTabKind, 'epic' | 'proposal' | 'story' | 'task'>;

/** 层级：section 为工作台一级视图，其余为实体层级。 */
export type WorkspaceTabLevel = 'section' | 'epic' | 'proposal' | 'story' | 'task';

/** 色标 token：与 CSS 变量 --ws-accent-* 对应。 */
export type WorkspaceTabAccent = 'neutral' | 'epic' | 'proposal' | 'story' | 'task';

export interface WorkspaceTab {
  /** 稳定 id: `${projectId}-${kind}` 或 `${projectId}-${kind}-${entityId}` */
  id: string;
  projectId: number;
  kind: WorkspaceTabKind;
  /** Entity tabs carry their record id; section tabs leave it unset. */
  entityId?: number;
  /** 显示文字 */
  title: string;
  /** SVG icon id (与 app.html 内联 SVG <use href="#i-xxx"> 一致) */
  iconId: string;
  /** 层级：决定缩进与分组 */
  level: WorkspaceTabLevel;
  /** 色标 */
  accent: WorkspaceTabAccent;
  /** 所属父 Tab（Story 的父是 Epic，Task 的父是 Story 或 Epic） */
  parentId?: string;
  /** 父级显示名（父 Tab 未打开时仍要显示面包屑） */
  parentTitle?: string;
  /** 固定：置顶显示且不被批量关闭清掉 */
  pinned: boolean;
  /** 最近一次访问时间戳（ms） */
  visitedAt: number;
}

/** Tab 条渲染视图：在 WorkspaceTab 上附带缩进深度。 */
export interface WorkspaceTabView extends WorkspaceTab {
  /** 0 = 顶层，1 = 缩进一级，最多 2 */
  depth: number;
  /** 是否处于某个已打开父 Tab 之下（用于决定要不要画连接线） */
  grouped: boolean;
}

/** 最近访问条目。 */
export interface WorkspaceRecentEntry {
  id: string;
  kind: WorkspaceTabKind;
  entityId?: number;
  title: string;
  parentTitle?: string;
  level: WorkspaceTabLevel;
  accent: WorkspaceTabAccent;
  path: string;
  visitedAt: number;
}

interface TabKindMeta {
  title: string;
  iconId: string;
  level: WorkspaceTabLevel;
  accent: WorkspaceTabAccent;
}

const TAB_META: Record<WorkspaceTabKind, TabKindMeta> = {
  overview:   { title: '概览',     iconId: 'i-home',     level: 'section',  accent: 'neutral' },
  kanban:     { title: '看板',     iconId: 'i-board',    level: 'section',  accent: 'neutral' },
  epics:      { title: 'Epics',   iconId: 'i-flag',     level: 'section',  accent: 'neutral' },
  backlog:    { title: '工作项',   iconId: 'i-list',     level: 'section',  accent: 'neutral' },
  proposals:  { title: '提案',     iconId: 'i-message',  level: 'section',  accent: 'neutral' },
  documents:  { title: '文档',     iconId: 'i-file',     level: 'section',  accent: 'neutral' },
  members:    { title: '成员与 Agents', iconId: 'i-users', level: 'section', accent: 'neutral' },
  settings:   { title: '设置',     iconId: 'i-settings', level: 'section',  accent: 'neutral' },
  epic:       { title: 'Epic',     iconId: 'i-flag',     level: 'epic',     accent: 'epic' },
  proposal:   { title: '提案',     iconId: 'i-message',  level: 'proposal', accent: 'proposal' },
  story:      { title: 'Story',    iconId: 'i-list',     level: 'story',    accent: 'story' },
  task:       { title: 'Task',     iconId: 'i-target',   level: 'task',     accent: 'task' },
};

const RECENT_LIMIT = 10;
const PINS_KEY_PREFIX = 'agentboard_ws_pins_';
const RECENT_KEY_PREFIX = 'agentboard_ws_recent_';

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
   * 最近访问（按时间倒序，最多 10 条）。
   * 只记录实体（epic / proposal / story / task）—— Section 侧边栏永远可达，
   * 记进去只会稀释「我刚才在看哪个 Story」这个真正有用的上下文。
   */
  private readonly _recent = signal<WorkspaceRecentEntry[]>([]);
  readonly recent = this._recent.asReadonly();

  /**
   * Tab 条渲染顺序：固定的置顶，其余按 parentId 聚拢到父 Tab 之后。
   * 打开顺序保持不变，只在需要时把子 Tab 挪到父 Tab 紧后面。
   */
  readonly displayTabs = computed<WorkspaceTabView[]>(() => {
    const list = this._tabs();
    if (!list.length) return [];
    const byParent = new Map<string, WorkspaceTab[]>();
    for (const tab of list) {
      if (!tab.parentId) continue;
      const bucket = byParent.get(tab.parentId);
      if (bucket) bucket.push(tab);
      else byParent.set(tab.parentId, [tab]);
    }
    const ordered: WorkspaceTabView[] = [];
    const emitted = new Set<string>();

    // 真正的根 = 没有父级的 Tab。父已打开的子 Tab 交给 walk 在父下面递归吐出，
    // 这样即使子 Tab 比父 Tab 先打开（很常见：先看 Story 再点它的 Epic），
    // 渲染顺序也会把子 Tab 归位到父 Tab 之后，而不是停在原地。
    // 先按 (pinned, 打开顺序) 排序，保证固定项置顶且其余顺序稳定。
    const roots = list
      .map((tab, index) => ({ tab, index }))
      .filter((entry) => !entry.tab.parentId)
      .sort((a, b) => {
        if (a.tab.pinned !== b.tab.pinned) return a.tab.pinned ? -1 : 1;
        return a.index - b.index;
      })
      .map((entry) => entry.tab);

    const walk = (tab: WorkspaceTab, depth: number, grouped: boolean): void => {
      if (emitted.has(tab.id)) return;
      emitted.add(tab.id);
      ordered.push({ ...tab, depth: Math.min(depth, 2), grouped });
      for (const child of byParent.get(tab.id) ?? []) {
        if (!emitted.has(child.id)) walk(child, depth + 1, true);
      }
    };

    for (const tab of roots) walk(tab, 0, false);

    // 父 Tab 未打开（或父 id 已失效）的孤儿 Tab：按打开顺序补在末尾并缩进一级。
    // 它们自己也可能带子 Tab（Story 没开、Task 挂在这个 Story 下的情形），
    // 所以同样走 walk，让子 Tab 继续缩进。
    for (const tab of list) {
      if (emitted.has(tab.id)) continue;
      walk(tab, 1, false);
    }
    return ordered;
  });

  /** 已固定的 tab（置顶区） */
  readonly pinnedTabs = computed(() => this.displayTabs().filter((t) => t.pinned));

  /**
   * 打开 / 激活一个 tab。
   * - 若已存在 → 激活即可
   * - 若不存在 → 新建并激活
   */
  openTab(projectId: number, kind: WorkspaceSectionTabKind): void {
    if (this._currentProjectId !== projectId) {
      this.setProject(projectId);
    }
    const id = this.makeId(projectId, kind);
    const existing = this._tabs().find((t) => t.id === id);
    if (existing) {
      this._activeId.set(id);
      this.touch(id);
      return;
    }
    const meta = TAB_META[kind];
    const tab: WorkspaceTab = {
      id,
      projectId,
      kind,
      title: meta.title,
      iconId: meta.iconId,
      level: meta.level,
      accent: meta.accent,
      pinned: this.isPinned(projectId, id),
      visitedAt: Date.now(),
    };
    this._tabs.update((list) => [...list, tab]);
    this._activeId.set(id);
  }

  /** Open or activate a concrete Epic / Proposal / Story / Task inside the project workspace. */
  openEntityTab(
    projectId: number,
    kind: WorkspaceEntityTabKind,
    entityId: number,
    title?: string,
    parent?: { id: string; title: string },
  ): void {
    if (this._currentProjectId !== projectId) {
      this.setProject(projectId);
    }
    const id = this.makeEntityId(projectId, kind, entityId);
    const existing = this._tabs().find((tab) => tab.id === id);
    if (existing) {
      if (title && existing.title !== title) this.updateTitle(id, title);
      if (parent && existing.parentId !== parent.id) this.setParent(id, parent.id, parent.title);
      this._activeId.set(id);
      this.touch(id);
      return;
    }
    const meta = TAB_META[kind];
    this._tabs.update((list) => [
      ...list,
      {
        id,
        projectId,
        kind,
        entityId,
        title: title || `${meta.title} #${entityId}`,
        iconId: meta.iconId,
        level: meta.level,
        accent: meta.accent,
        parentId: parent?.id,
        parentTitle: parent?.title,
        pinned: this.isPinned(projectId, id),
        visitedAt: Date.now(),
      },
    ]);
    this._activeId.set(id);
    this.recordRecent({
      id,
      kind,
      entityId,
      title: title || `${meta.title} #${entityId}`,
      parentTitle: parent?.title,
      level: meta.level,
      accent: meta.accent,
      path: this.pathFor(projectId, kind, entityId),
      visitedAt: Date.now(),
    });
  }

  /** Refresh an entity tab label after its detail payload has loaded. */
  updateTitle(id: string, title: string): void {
    const nextTitle = title.trim();
    if (!nextTitle) return;
    this._tabs.update((list) =>
      list.map((tab) => (tab.id === id ? { ...tab, title: nextTitle } : tab)),
    );
    this._recent.update((list) =>
      list.map((entry) => (entry.id === id ? { ...entry, title: nextTitle } : entry)),
    );
  }

  /**
   * 绑定父级（详情加载完成后才知道 Task 属于哪个 Story / Epic）。
   * 父级可能是 Story Tab，Story Tab 本身又挂在 Epic Tab 下，形成两级缩进。
   */
  setParent(id: string, parentId: string | undefined, parentTitle?: string): void {
    this._tabs.update((list) =>
      list.map((tab) => (tab.id === id ? { ...tab, parentId, parentTitle } : tab)),
    );
    this._recent.update((list) =>
      list.map((entry) => (entry.id === id ? { ...entry, parentTitle } : entry)),
    );
  }

  /** 关闭一个 tab。激活其相邻 tab（左侧优先，无则右侧，无则 null） */
  closeTab(id: string): void {
    const list = this._tabs();
    const idx = list.findIndex((t) => t.id === id);
    if (idx < 0) return;
    const target = list[idx];
    if (target.pinned) this.unpin(id);
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
    this.touch(id);
    return true;
  }

  /**
   * 切换项目 → 清空所有 tab，并载入该项目的固定 / 最近访问持久化数据。
   * 调用方应在导航进入新项目时调用。
   */
  setProject(projectId: number): void {
    if (this._currentProjectId === projectId) return;
    this._currentProjectId = projectId;
    this._tabs.set([]);
    this._activeId.set(null);
    this._pinnedIds = this.readIds(PINS_KEY_PREFIX + projectId);
    this._recent.set(this.readRecent(RECENT_KEY_PREFIX + projectId));
  }

  /** 关闭除指定外的所有 tab（菜单 "关闭其他"）；固定项保留 */
  closeOthers(id: string): void {
    const keep = this._tabs().find((t) => t.id === id);
    if (!keep) return;
    const kept = this._tabs().filter((t) => t.id === id || t.pinned);
    this._tabs.set(kept);
    this._activeId.set(id);
  }

  /** 关闭所有可关闭的 tab（固定项保留） */
  closeAll(): void {
    const kept = this._tabs().filter((t) => t.pinned);
    this._tabs.set(kept);
    this._activeId.set(kept.length ? kept[0].id : null);
  }

  // ─── 固定（Pin） ────────────────────────────────────────────────────

  private _pinnedIds = new Set<string>();

  isPinned(projectId: number, id: string): boolean {
    if (this._currentProjectId !== projectId) return false;
    return this._pinnedIds.has(id);
  }

  togglePin(id: string): void {
    const tab = this._tabs().find((t) => t.id === id);
    if (!tab) return;
    if (tab.pinned) this.unpin(id);
    else this.pin(id);
  }

  private pin(id: string): void {
    this._tabs.update((list) =>
      list.map((tab) => (tab.id === id ? { ...tab, pinned: true } : tab)),
    );
    this._pinnedIds.add(id);
    this.writeIds();
  }

  private unpin(id: string): void {
    this._tabs.update((list) =>
      list.map((tab) => (tab.id === id ? { ...tab, pinned: false } : tab)),
    );
    this._pinnedIds.delete(id);
    this.writeIds();
  }

  // ─── 最近访问 ───────────────────────────────────────────────────────

  private recordRecent(entry: WorkspaceRecentEntry): void {
    const deduped = this._recent().filter((item) => item.id !== entry.id);
    this._recent.set([entry, ...deduped].slice(0, RECENT_LIMIT));
    this.writeRecent();
  }

  /** 从最近访问里移除一条（Tab 关闭时不同步删除——历史应当保留） */
  clearRecent(): void {
    this._recent.set([]);
    this.writeRecent();
  }

  private touch(id: string): void {
    this._tabs.update((list) =>
      list.map((tab) => (tab.id === id ? { ...tab, visitedAt: Date.now() } : tab)),
    );
    const match = this._recent().find((entry) => entry.id === id);
    if (match) this.recordRecent({ ...match, visitedAt: Date.now() });
  }

  /** 实体 tab 的直链路径（与 shell 的 replaceUrl 保持一致） */
  private pathFor(
    projectId: number,
    kind: WorkspaceEntityTabKind,
    entityId: number,
  ): string {
    const sections: Record<WorkspaceEntityTabKind, string> = {
      epic: 'epics',
      proposal: 'proposals',
      story: 'stories',
      task: 'tasks',
    };
    return `/project/${projectId}/${sections[kind]}/${entityId}`;
  }

  // ─── localStorage 持久化（按项目隔离，SSR / 无 storage 时静默降级） ──

  private writeIds(): void {
    if (this._currentProjectId === null) return;
    this.writeIdsTo(PINS_KEY_PREFIX + this._currentProjectId, [...this._pinnedIds]);
  }

  private writeRecent(): void {
    if (this._currentProjectId === null) return;
    this.writeJson(RECENT_KEY_PREFIX + this._currentProjectId, this._recent());
  }

  private readIds(key: string): Set<string> {
    const raw = this.readJson<unknown>(key);
    return new Set(Array.isArray(raw) ? raw.filter((v): v is string => typeof v === 'string') : []);
  }

  private readRecent(key: string): WorkspaceRecentEntry[] {
    const raw = this.readJson<unknown>(key);
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((item): item is WorkspaceRecentEntry => !!item && typeof (item as WorkspaceRecentEntry).id === 'string')
      .slice(0, RECENT_LIMIT);
  }

  private readJson<T>(key: string): T | null {
    if (typeof localStorage === 'undefined') return null;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  }

  private writeJson(key: string, value: unknown): void {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* 配额满 / 隐私模式：静默降级，不阻断 UI */
    }
  }

  private writeIdsTo(key: string, ids: string[]): void {
    this.writeJson(key, ids);
  }

  // ─── 工具 ──────────────────────────────────────────────────────────

  /** 由 (projectId, kind) 推算 id — 暴露给模板 / 路由同步使用 */
  makeId(projectId: number, kind: WorkspaceSectionTabKind): string {
    return `${projectId}-${kind}`;
  }

  makeEntityId(projectId: number, kind: WorkspaceEntityTabKind, entityId: number): string {
    return `${projectId}-${kind}-${entityId}`;
  }

  /** 工具方法：根据 kind 拿到展示元信息 */
  meta(kind: WorkspaceTabKind): TabKindMeta {
    return TAB_META[kind];
  }
}
