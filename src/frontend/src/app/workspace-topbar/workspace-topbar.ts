import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import type { Project, Notification } from '../models';

/**
 * WorkspaceTopbarComponent — Epic 150 / Story 323 (X2) Workspace topbar 框架
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/agentboard-home-workspace.html §34-75）：
 * 复刻 prototype v7 的 workspace topbar 左侧：返回全部项目 + 项目切换器（monogram + 名称 + 下拉）。
 * 不含：sidebar hamburger（属外层 layout）、主题切换/通知/用户菜单（属外层 layout 右侧）。
 *
 * 数据契约（@Input）：
 *   project  Project | null — 当前项目（用于 monogram + 名称 + 切换器下拉）
 *   projects Project[]  — 项目列表（用于切换器弹层内的可选项）
 *   showSwitcher boolean — 项目切换器下拉是否展开
 *   switcherSearch string — 切换器搜索关键词
 *
 * 事件契约（@Output）：
 *   back               void       — 点击「返回全部项目」
 *   toggleSwitcher     void       — 点击项目切换器（父级控制 showSwitcher）
 *   selectProject      number     — 切换器选中某项目
 *   addProject         void       — 「+ 添加新项目」点击
 *   searchChange       string     — 切换器搜索框变化
 *   overlayClick       void       — 弹层外区点击
 *
 * 视觉：与 prototype 1:1 — 「← 全部项目」+ monogram + 项目名 + chevron 下拉。
 *
 * ViewEncapsulation.None：与全局 .topbar / .back-button-v7 / .project-switcher-button-v7 等基础类共享。
 */
@Component({
  selector: 'app-workspace-topbar',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './workspace-topbar.html',
  styleUrl: './workspace-topbar.css',
  encapsulation: ViewEncapsulation.None,
})
export class WorkspaceTopbarComponent {
  @Input() project: Project | null = null;
  @Input() projects: Project[] = [];
  @Input() showSwitcher = false;
  @Input() switcherSearch = '';
  /** 是否实际渲染。父级通过 *ngIf 控制，避免组件在非工作台视图时无谓执行。 */
  @Input() visible = true;

  @Output() back = new EventEmitter<void>();
  @Output() toggleSwitcherEvent = new EventEmitter<void>();
  @Output() selectProject = new EventEmitter<number>();
  @Output() addProject = new EventEmitter<void>();
  @Output() searchChange = new EventEmitter<string>();
  @Output() overlayClick = new EventEmitter<void>();

  /** 当前项目 monogram（前 2 字符大写）。 */
  monogram(): string {
    return this.monogramFor(this.project);
  }

  /** 任意项目的 monogram（前 2 字符大写）。 */
  monogramFor(p: Project | null): string {
    if (!p) return 'AB';
    const name = p.name || p.key || 'AB';
    return name.slice(0, 2).toUpperCase();
  }

  /** 当前项目名。 */
  projectName(): string {
    if (!this.project) return '未选择项目';
    return this.project.name || '未命名项目';
  }

  /** 切换器搜索过滤后的项目列表。 */
  filteredProjects(): Project[] {
    const q = (this.switcherSearch || '').trim().toLowerCase();
    if (!q) return this.projects;
    return this.projects.filter((p) =>
      (p.name || '').toLowerCase().includes(q) ||
      (p.key || '').toLowerCase().includes(q)
    );
  }

  /** 是否当前项目（用于切换器内 ✓ 标记）。 */
  isCurrent(p: Project): boolean {
    return !!this.project && this.project.id === p.id;
  }

  onToggleSwitcher(): void {
    this.toggleSwitcherEvent.emit();
  }

  onSelectProject(id: number): void {
    this.selectProject.emit(id);
  }

  onAddProject(): void {
    this.addProject.emit();
  }

  onBack(): void {
    this.back.emit();
  }

  onSearchInput(value: string): void {
    this.searchChange.emit(value);
  }

  onOverlayClick(): void {
    this.overlayClick.emit();
  }

  /** *ngFor trackBy（按 id 稳定追踪）。 */
  trackById = (_index: number, item: { id: number }): number => item.id;
}
