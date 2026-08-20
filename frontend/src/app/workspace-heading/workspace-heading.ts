import { Component, Input, ViewEncapsulation } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * WorkspaceHeadingComponent — Epic 150 / Story 323 (X2) Workspace heading 框架
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/agentboard-home-workspace.html §80-95）：
 * 复刻 prototype v7 的页面标题区：左（eyebrow 小标 + h1 大标题 + 副标题） + 右（操作按钮 slot）。
 *
 * 数据契约（@Input）：
 *   eyebrow    string — 小标（如 "PROJECT CENTER"）
 *   title      string — 大标题（h1）
 *   subtitle   string — 副标题（h1 下方一行 muted）
 *   visible    boolean — 是否实际渲染
 *
 * 视觉：左/右 flex 分布，右槽用 <ng-content select="[actions]"></ng-content> 投影。
 *
 * 适用：X3 阶段会替换 8 个视图的 page-header（X2 阶段只建组件 + 单元验证）。
 *
 * ViewEncapsulation.None：与全局 .page-header / .eyebrow / .muted 等基础类共享。
 */
@Component({
  selector: 'app-workspace-heading',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './workspace-heading.html',
  styleUrl: './workspace-heading.css',
  encapsulation: ViewEncapsulation.None,
})
export class WorkspaceHeadingComponent {
  @Input() eyebrow = '';
  @Input() title = '';
  @Input() subtitle = '';
  @Input() visible = true;
}
