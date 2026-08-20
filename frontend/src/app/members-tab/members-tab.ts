import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { ProjectMember, AgentRow } from '../models';

/**
 * MembersTabComponent — Epic 149 Bug #1290 修复：
 * 项目「成员与 Agents」视图独立组件，补全 Epic 149 阶段2/3 漏迁的最后一个 tab（9/9）。
 *
 * 背景：
 *   阶段2（Story 318）5 列表抽 ManagedListComponent 时漏 members；
 *   阶段3（Story 319）8 视图从 @switch 拆独立组件时也漏 members（提交 099eff0
 *   自称 "8/8 final" 但实际是 stats 而非 members）。
 *   表现：点击侧边栏「成员与 Agents」activeTab 切到 'members'，但 app.html 主内容区
 *   无对应 @if 渲染块，故主区完全空白。
 *
 * 数据契约（@Input）：
 *   members   项目成员列表（来自 App.members()）
 *   agents    全局 Agent 池（来自 App.agents()，与项目无关）
 *   loading   members tab 是否加载中（App.isProjectTabLoading('members')）
 *   error     members tab 加载错误（App.projectTabError('members')）
 *
 * 事件契约（@Output）：
 *   retry     重试加载（替代 App.retryProjectTab('members')）
 *
 * 视觉：
 *   - 套 ManagedListComponent 外壳（loading/error/空态）
 *   - 两段：上半「项目成员」表格，下半「项目相关 Agents」表格
 *   - v7 增强：表格行 hover brand 描边、role badge 提色（owner=navy / member=muted）
 *   - 暗色主题：表格表头 navy 提亮、文字降饱和
 */
@Component({
  selector: 'app-members-tab',
  standalone: true,
  imports: [CommonModule, ManagedListComponent, WorkspaceHeadingComponent],
  templateUrl: './members-tab.html',
  styleUrl: './members-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class MembersTabComponent {
  @Input({ required: true }) members: ProjectMember[] = [];
  @Input({ required: true }) agents: AgentRow[] = [];
  @Input() loading = false;
  @Input() error: string | null = null;

  @Output() retry = new EventEmitter<void>();

  /** 角色可读标签。 */
  memberRoleLabel(role: ProjectMember['role']): string {
    return role === 'owner' ? '所有者' : '成员';
  }

  /** 角色 CSS 修饰类。 */
  memberRoleClass(role: ProjectMember['role']): string {
    return role === 'owner' ? 'role-owner' : 'role-member';
  }

  /** 用户显示名（username 为 null 时回退到 user_id）。 */
  memberDisplayName(m: ProjectMember): string {
    return m.username || `user-${m.user_id}`;
  }

  /** Agent 角色 JSON 串 → 数组（容错解析）。 */
  agentRoles(a: AgentRow): string[] {
    if (!a.roles) return [];
    try {
      const parsed = JSON.parse(a.roles);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  /** 相对时间（与 App.timeAgo 一致，纯函数复制避免子组件依赖父级）。 */
  timeAgo(dateStr: string | null | undefined): string {
    if (!dateStr) return '从未';
    const date = new Date(dateStr).getTime();
    if (Number.isNaN(date)) return '从未';
    const diff = Math.floor((Date.now() - date) / 1000);
    if (diff < 60) return `${diff}s 前`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d 前`;
    return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  }
}
