import { Component, inject, Input, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../api.service';
import { OverviewStats } from '../models';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';

export type StatsEntity = 'epics' | 'stories' | 'tasks' | 'bugs' | 'dashboard';

/**
 * 视图层扁平聚合：把 OverviewStats.counts 拍平 + 补 bugs(0 占位)。
 * OverviewStats 本身含 projects 数组、status_distribution、activity_7d 等，
 * 这层视图不消费，故不暴露到模板。
 */
interface OverviewSnapshot {
  projects: number;
  epics: number;
  stories: number;
  tasks: number;
  bugs: number;
}

function toSnapshot(stats: OverviewStats): OverviewSnapshot {
  // counts 字段是 OverviewStats 的必填契约；如后端漏字段，TS 编译期就拦下。
  return {
    projects: stats.counts.projects,
    epics: stats.counts.epics,
    stories: stats.counts.stories,
    tasks: stats.counts.tasks,
    bugs: 0, // OverviewStats 未含 bugs 维度；等后端补
  };
}

/**
 * GlobalStatsTabComponent — #1430 全局统计聚合视图
 *
 * 5 个路由（/epics /stories /tasks /bugs /dashboard）共用此组件，
 * @Input entity 决定标题文案 + jump-card 高亮。
 * 内部调 /api/overview 拉聚合统计。
 */
@Component({
  selector: 'app-global-stats-tab',
  standalone: true,
  imports: [CommonModule, RouterLink, WorkspaceHeadingComponent],
  templateUrl: './global-stats-tab.html',
  styleUrl: './global-stats-tab.css',
})
export class GlobalStatsTabComponent implements OnInit {
  @Input({ required: true }) entity!: StatsEntity;

  private readonly api = inject(ApiService);
  readonly snapshot = signal<OverviewSnapshot | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');

  get title(): string {
    return ENTITY_TITLES[this.entity] ?? '全局概览';
  }
  get subtitle(): string {
    return ENTITY_SUBTITLES[this.entity] ?? '跨项目聚合统计与快捷入口。';
  }
  get activeEntity(): StatsEntity {
    return this.entity;
  }

  async ngOnInit(): Promise<void> {
    this.loading.set(true);
    this.error.set('');
    try {
      const data = await firstValueFrom(this.api.getOverview());
      this.snapshot.set(toSnapshot(data));
    } catch (e: any) {
      this.error.set(e?.error?.detail || e?.message || '加载概览失败');
      this.snapshot.set(null);
    } finally {
      this.loading.set(false);
    }
  }
}

const ENTITY_TITLES: Record<StatsEntity, string> = {
  epics: '全局 Epics 概览',
  stories: '全局 Stories 概览',
  tasks: '全局 Tasks 概览',
  bugs: '全局 Bugs 概览',
  dashboard: '项目大脑 / 总览',
};

const ENTITY_SUBTITLES: Record<StatsEntity, string> = {
  epics: '按业务目标组织的跨项目 Epic 统计。',
  stories: '按状态聚合的所有项目 Story 概览。',
  tasks: '跨项目任务与交付状态概览。',
  bugs: '跨项目缺陷与修复状态概览。',
  dashboard: '系统总览：项目、Epic、Story、Task、缺陷全维度统计。',
};
