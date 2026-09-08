import { beforeEach, describe, expect, it } from 'vitest';
import { WorkspaceTabsService } from './workspace-tabs.service';

describe('WorkspaceTabsService entity tabs', () => {
  it('deduplicates the same Epic and keeps different Epics independently open', () => {
    const service = new WorkspaceTabsService();

    service.openTab(7, 'epics');
    service.openEntityTab(7, 'epic', 152, 'Epic · 工作台改造');
    service.openEntityTab(7, 'epic', 152, 'Epic · 工作台改造');
    service.openEntityTab(7, 'epic', 153);

    expect(service.tabs().map((tab) => tab.id)).toEqual([
      '7-epics',
      '7-epic-152',
      '7-epic-153',
    ]);
    expect(service.activeTab()?.entityId).toBe(153);
  });

  it('updates an entity title after detail data loads', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'proposal', 96);

    service.updateTitle('7-proposal-96', '提案 · Agent 协作方案');

    expect(service.activeTab()?.title).toBe('提案 · Agent 协作方案');
  });

  it('clears section and entity tabs together when switching projects', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'proposals');
    service.openEntityTab(7, 'proposal', 96);

    service.setProject(8);

    expect(service.tabs()).toEqual([]);
    expect(service.activeTab()).toBeNull();
  });

  it('keeps Story and Task tabs distinct and deduplicates each entity', () => {
    const service = new WorkspaceTabsService();

    service.openEntityTab(7, 'story', 289, 'Story · 工作台导航');
    service.openEntityTab(7, 'task', 1322, 'Task · 接入详情 Tab');
    service.openEntityTab(7, 'story', 289, 'Story · 工作台导航');

    expect(service.tabs().map((tab) => tab.id)).toEqual([
      '7-story-289',
      '7-task-1322',
    ]);
    expect(service.activeTab()?.kind).toBe('story');
    expect(service.activeTab()?.entityId).toBe(289);
  });
});

describe('WorkspaceTabsService hierarchy (Story #435)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('groups child tabs under their parent and reports indent depth', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'backlog');
    service.openEntityTab(7, 'epic', 31, 'Epic 31');
    service.openEntityTab(7, 'story', 434, 'Story A', { id: '7-epic-31', title: 'Epic 31' });
    service.openEntityTab(7, 'task', 1001, 'Task X', { id: '7-story-434', title: 'Story A' });

    const view = service.displayTabs();
    expect(view.map((v) => v.id)).toEqual([
      '7-backlog',
      '7-epic-31',
      '7-story-434',
      '7-task-1001',
    ]);
    expect(view.map((v) => v.depth)).toEqual([0, 0, 1, 2]);
    expect(view[2].grouped).toBe(true);
    expect(view[3].grouped).toBe(true);
  });

  it('re-groups a child under its parent even when the child was opened first', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'task', 1001, 'Task X', { id: '7-epic-31', title: 'Epic 31' });
    service.openEntityTab(7, 'epic', 31, 'Epic 31');

    const view = service.displayTabs();
    expect(view.map((v) => v.id)).toEqual(['7-epic-31', '7-task-1001']);
    expect(view.map((v) => v.depth)).toEqual([0, 1]);
  });

  it('keeps an orphaned child visible with one indent level when its parent is closed', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'task', 55, 'Task 55', { id: '7-story-999', title: '未打开的 Story' });

    const view = service.displayTabs();
    expect(view.map((v) => v.id)).toEqual(['7-task-55']);
    expect(view[0].depth).toBe(1);
    expect(view[0].grouped).toBe(false);
  });

  it('carries level and accent metadata per tab kind', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'backlog');
    service.openEntityTab(7, 'epic', 31);
    service.openEntityTab(7, 'task', 1);

    const byKind = new Map(service.tabs().map((t) => [t.kind, t]));
    expect(byKind.get('backlog')?.level).toBe('section');
    expect(byKind.get('epic')?.accent).toBe('epic');
    expect(byKind.get('task')?.accent).toBe('task');
    expect(byKind.get('task')?.level).toBe('task');
  });

  it('re-parents a tab after its detail payload resolves', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'task', 1001, 'Task X');
    expect(service.tabs()[0].parentId).toBeUndefined();

    service.setParent('7-task-1001', '7-story-434', 'Story A');

    expect(service.tabs()[0].parentId).toBe('7-story-434');
    expect(service.tabs()[0].parentTitle).toBe('Story A');
  });
});

describe('WorkspaceTabsService pinning (Story #435)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('floats pinned tabs to the front of the strip', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'overview');
    service.openEntityTab(7, 'epic', 31, 'Epic 31');

    service.togglePin('7-epic-31');

    expect(service.displayTabs()[0].id).toBe('7-epic-31');
    expect(service.displayTabs()[0].pinned).toBe(true);
  });

  it('keeps pinned tabs when closing all, and unpinning restores closability', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'overview');
    service.openEntityTab(7, 'epic', 31, 'Epic 31');
    service.togglePin('7-epic-31');

    service.closeAll();
    expect(service.tabs().map((t) => t.id)).toEqual(['7-epic-31']);

    service.togglePin('7-epic-31');
    service.closeAll();
    expect(service.tabs()).toEqual([]);
  });

  it('keeps pinned tabs and the target when closing others', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'overview');
    service.openEntityTab(7, 'epic', 31, 'Epic 31');
    service.openEntityTab(7, 'epic', 32, 'Epic 32');
    service.togglePin('7-epic-31');

    service.closeOthers('7-epic-32');

    expect(service.tabs().map((t) => t.id).sort()).toEqual(['7-epic-31', '7-epic-32']);
    expect(service.activeTab()?.id).toBe('7-epic-32');
  });

  it('scopes pin persistence per project', () => {
    const service = new WorkspaceTabsService();
    service.setProject(7);
    service.openTab(7, 'overview');
    service.togglePin('7-overview');

    service.setProject(8);
    service.openTab(8, 'overview');
    expect(service.displayTabs()[0].pinned).toBe(false);

    service.setProject(7);
    service.openTab(7, 'overview');
    expect(service.displayTabs()[0].pinned).toBe(true);
  });
});

describe('WorkspaceTabsService recent context (Story #435)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('records entity visits with their deep-link path, newest first', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'story', 434, 'Story · 文档快速定位');
    service.openEntityTab(7, 'task', 1001, 'Task · QA 验证');

    expect(service.recent().map((e) => e.id)).toEqual(['7-task-1001', '7-story-434']);
    expect(service.recent()[1].path).toBe('/project/7/stories/434');
    expect(service.recent()[1].level).toBe('story');
  });

  it('re-activating a known entity moves it to the top instead of duplicating', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'story', 434, 'Story A');
    service.openEntityTab(7, 'task', 1, 'Task B');

    service.activateTab('7-story-434');

    expect(service.recent().map((e) => e.id)).toEqual(['7-story-434', '7-task-1']);
  });

  it('does not record section tabs into recent context', () => {
    const service = new WorkspaceTabsService();
    service.openTab(7, 'overview');
    service.openTab(7, 'backlog');

    expect(service.recent()).toEqual([]);
  });

  it('restores recent context after switching back to a project', () => {
    const service = new WorkspaceTabsService();
    service.openEntityTab(7, 'epic', 31, 'Epic 31');
    service.setProject(8);
    expect(service.recent()).toEqual([]);

    service.setProject(7);
    expect(service.recent().map((e) => e.id)).toEqual(['7-epic-31']);
  });

  it('records a Drawer-only entity visit into recent without creating a tab (#435 P2)', () => {
    const service = new WorkspaceTabsService();
    service.setProject(7);
    // Drawer 路径：不建 Tab，仅记录一次实体访问
    service.recordEntityVisit(7, 'task', 1001, 'Task · QA 验证');

    expect(service.tabs()).toEqual([]);
    expect(service.recent().map((e) => e.id)).toEqual(['7-task-1001']);
    expect(service.recent()[0].path).toBe('/project/7/tasks/1001');
    expect(service.recent()[0].level).toBe('task');
  });

  it('re-visiting a known entity via Drawer bumps it to top and preserves the richer title', () => {
    const service = new WorkspaceTabsService();
    service.setProject(7);
    service.openEntityTab(7, 'story', 434, 'Story · 完整标题');
    service.openEntityTab(7, 'task', 1001, 'Task X');

    // 不传标题：已存在条目应仅刷新排序，不被占位标题覆盖
    service.recordEntityVisit(7, 'story', 434);

    expect(service.recent().map((e) => e.id)).toEqual(['7-story-434', '7-task-1001']);
    expect(service.recent()[0].title).toBe('Story · 完整标题');
  });
});
