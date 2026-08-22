import { describe, expect, it } from 'vitest';
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
