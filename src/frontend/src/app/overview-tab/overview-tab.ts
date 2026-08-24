import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { Project, Epic, ProjectMember, ProjectTabKind } from '../models';

/**
 * OverviewTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「概览」视图独立组件。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区落地页（overview tab）从 4900+ 行的单体模板中拆出，套用原型 v7 的
 * metric-card / workspace-card 视觉骨架，同时保留原有业务逻辑（项目信息、计数卡片、
 * 成员/Epic/快捷入口）。
 *
 * 数据契约（@Input）：
 *   project       当前项目（非空，由父组件 @if 保证）
 *   members       项目成员列表
 *   epics         项目 Epic 列表
 *   backlogCount  待办任务计数
 *   kanbanCount   看板卡片计数
 *   proposalCount 活跃提案计数
 *
 * 事件契约（@Output）：
 *   navigateTab  切换项目内 tab（替代 App.selectProjectTab）
 *   goEpic       跳转 Epic 详情（替代 App.goEpic）
 *
 * 视觉：stat 卡片套用原型 metric-card 风格（label+icon 行 / navy 大数字 / foot 提示），
 * 信息卡片套用 workspace-card 风格（head h2 + body）。样式全局（ViewEncapsulation.None），
 * 与 App 组件封装策略一致，过渡期 app.css 旧规则共存，阶段4 统一清理。
 */
@Component({
  selector: 'app-overview-tab',
  standalone: true,
  imports: [WorkspaceHeadingComponent],
  templateUrl: './overview-tab.html',
  styleUrl: './overview-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class OverviewTabComponent {
  @Input({ required: true }) project!: Project;
  @Input({ required: true }) members: ProjectMember[] = [];
  @Input({ required: true }) epics: Epic[] = [];
  @Input() backlogCount = 0;
  @Input() kanbanCount = 0;
  @Input() proposalCount = 0;

  @Output() navigateTab = new EventEmitter<ProjectTabKind>();
  @Output() goEpic = new EventEmitter<number>();

  /** 日期格式化（与 App.formatDate 一致，纯函数复制以避免跨组件依赖）。 */
  formatDate(dateStr: string | null | undefined): string {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '';
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  }
}
