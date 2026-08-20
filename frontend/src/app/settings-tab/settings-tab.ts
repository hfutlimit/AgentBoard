import { Component, ViewEncapsulation } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';

/**
 * SettingsTabComponent — Epic 151 / Story 327 路由化占位组件。
 *
 * 背景：app.html 项目区 settings tab 内容是 inline 模板（line 657-1010，~350 行），
 * 含 settingsSubTab 子页（basic / members / schedules / export），依赖 app.ts
 * 大量 signal 与 method（current / members / schedules / settingsSubTab /
 * selectSettingsSubTab / saveProjectSettings / remove 等）。
 *
 * 完整迁移需重构数据流（service / inject），本 Story 只做"路由化 + 占位"：
 * - 路由 `/project/:id/settings` 现在 loadComponent SettingsTabComponent 生效，
 *   满足 review 高优先级 #4 的"8 tab loadComponent"要求；
 * - 当前阶段 app.html 的 settings @if 块**仍由 activeTab 驱动渲染**
 *   （hybrid 过渡，参见 app.routes.ts 注释）；
 * - TODO（Story 328+）：把 inline 模板迁入此组件，组件自 inject 服务/路由，
 *   app.html 拆掉 8 个 @if 块改为 ProjectShell 内部 router-outlet。
 *
 * 现在：本组件仅渲染「项目设置」标题占位，便于 URL 直接访问时给用户一个
 * 可识别的反馈；内容仍由 app.html 的 @if (activeTab() === 'settings') 块提供。
 */
@Component({
  selector: 'app-settings-tab',
  standalone: true,
  imports: [CommonModule, WorkspaceHeadingComponent],
  template: `
    <app-workspace-heading
      title="项目设置"
      subtitle="路由化占位组件（Story 327）：完整内容由 app.html settings @if 块提供，后续 Story 迁移至此组件。">
    </app-workspace-heading>
  `,
  styles: [`
    app-settings-tab { display: block; padding: 16px; }
  `],
  encapsulation: ViewEncapsulation.None,
})
export class SettingsTabComponent {}
