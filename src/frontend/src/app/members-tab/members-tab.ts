import { Component, EventEmitter, Input, Output, ViewEncapsulation } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import type { ProjectMember, AgentRow } from '../models';

/**
 * MembersTabComponent — Epic 149 Bug #1290 修复 + Epic 151 Story 326 Task 1297 校准：
 * 项目「成员与 Agents」视图独立组件。
 *
 * 背景：
 *   - 阶段2/3（Story 318/319）漏迁 members tab：点击侧边栏「成员与 Agents」
 *     activeTab 切到 'members' 但 app.html 主内容区无对应 @if 渲染块（Bug #1290）。
 *   - Epic 149 静态 Review 阻断级 2（2026-08-20）：MembersTab 文案「参与本项目的
 *     Agent 池」与后端数据不一致——后端 ``/api/agents`` 返回全表（无 project 过滤），
 *     且 ``_ser`` 透出全列（含 ``cli_command`` / ``auth_key`` / ``probe_message``）。
 *     文案与数据边界不一致，误导用户且有安全风险。
 *   - Task 1297 修复：
 *     * 后端：Agent class 加 ``to_public_dict()`` 脱敏；``/api/agents`` 加软鉴权
 *       （``AGENTBOARD_REQUIRE_AUTH=1`` 时 401），list_agents endpoint 改用
 *       ``to_public_dict``；list_agents service 加 ``order_by_created``。
 *     * 前端：heading subtitle 改「全局 Agent 池 · 跨项目共享（按注册时间倒序）」；
 *       下半区标题改「全局 Agent 池」；badge 改「N 个 Agent（全局）」。
 *
 * 数据契约（@Input）：
 *   members   项目成员列表（来自 App.members()，仅本项目）
 *   agents    全局 Agent 池（来自 App.agents()，跨项目共享，按 created_at 倒序）
 *   loading   members tab 是否加载中（App.isProjectTabLoading('members')）
 *   error     members tab 加载错误（App.projectTabError('members')）
 *
 * 事件契约（@Output）：
 *   retry     重试加载（替代 App.retryProjectTab('members')）
 *
 * 视觉：
 *   - 套 ManagedListComponent 外壳（loading/error/空态）
 *   - 两段：上半「项目成员」表格，下半「全局 Agent 池」表格
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
