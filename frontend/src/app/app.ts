import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, ViewEncapsulation, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';
import { DOCUMENT } from '@angular/common';
import { Inject } from '@angular/core';
import { filter } from 'rxjs/operators';

import { ApiService, AUTH_EXPIRED_EVENT, OFFLINE_QUEUE_FLUSH_EVENT, perfTracker, ApiMetric } from './api.service';
import { LoginComponent } from './login/login';
import { AgentSchedule, ApiKeyInfo, Attachment, AuditLog, Comment, Epic, ItemType, Notification, Priority, Project, ProjectMember, ProjectStats, Sprint, SprintStatus, Status, Story, Task, TaskDependencies, UserProfile, WebhookConfig, DocumentItem, DocumentCommentItem, DocumentType, DocumentStatus, DOCUMENT_TYPES, DOCUMENT_STATUSES } from './models';
import { PaginationComponent } from './pagination/pagination';

type ViewKind = 'home' | 'projects' | 'project' | 'epic' | 'story' | 'task' | 'sprint' | 'documents' | 'document' | 'admin' | 'settings' | 'not-found';
type CreateKind = 'project' | 'epic' | 'story' | 'task';
type ProjectTabKind = 'epics' | 'sprints' | 'backlog' | 'settings' | 'members' | 'stats' | 'schedules' | 'documents';
type ProjectListKind = 'epics' | 'sprints' | 'backlog' | 'members' | 'schedules';

interface CreateModal {
  kind: CreateKind;
  parentId?: number;
  projectId?: number;
}

type ConfirmationTone = 'danger' | 'warning' | 'info';

// v3.1: 筛选预设 —— 保存当前筛选组合（5 维度 chips + 搜索 + 只看我），localStorage 持久化
interface FilterPreset {
  name: string;
  status: string;      // ''=全部
  priority: string;    // ''=全部
  type: string;        // ''=全部
  assignee: string;    // ''=全部（user_id 字符串）
  due: string;         // ''=全部（overdue/today/week/none）
  search: string;
  mineOnly: boolean;
}

interface ConfirmationDialog {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  tone: ConfirmationTone;
  action: () => Promise<void>;
}

@Component({
  selector: 'app-root',
  imports: [CommonModule, FormsModule, RouterLink, RouterOutlet, LoginComponent, PaginationComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
  encapsulation: ViewEncapsulation.None,
})
export class App implements OnInit, OnDestroy {
  readonly projects = signal<Project[]>([]);
  readonly recentProjects = signal<Project[]>([]);
  readonly favoriteProjects = signal<Project[]>([]);
  private recentProjectIds: number[] = [];
  private favoriteProjectIds: Set<number> = new Set();
  readonly epics = signal<Epic[]>([]);
  readonly stories = signal<Story[]>([]);
  readonly tasks = signal<Task[]>([]);
  readonly comments = signal<Comment[]>([]);
  readonly sprints = signal<Sprint[]>([]);
  readonly sprint = signal<Sprint | null>(null);
  readonly sprintTasks = signal<Task[]>([]);
  readonly backlogTasks = signal<Task[]>([]);
  readonly sprintBurndown = signal<any>(null);
  readonly project = signal<Project | null>(null);
  readonly epic = signal<Epic | null>(null);
  readonly story = signal<Story | null>(null);
  readonly task = signal<Task | null>(null);
  readonly view = signal<ViewKind>('home');
  readonly loading = signal(true);
  readonly error = signal('');
  readonly search = signal('');
  readonly sidebarOpen = signal(true);
  readonly boardMode = signal(localStorage.getItem('agentboard_story_view') === 'board');
  // Task 831: 列表密度切换（舒适 / 紧凑），偏好持久化
  readonly listDensity = signal<'comfortable' | 'compact'>(
    (localStorage.getItem('agentboard_list_density') as 'comfortable' | 'compact') || 'comfortable'
  );
  readonly authVisible = signal(!localStorage.getItem('agentboard_token'));
  readonly authMode = signal<'login' | 'register'>('login');
  readonly currentUser = signal(localStorage.getItem('agentboard_user') || '');
  readonly toastMessage = signal('');
  readonly toastType = signal<'success' | 'error'>('success');
  // Epic 24 Story 24.2: Toast 增强 - 多 toasts 支持
  private _toastCounter = 0;
  readonly toasts = signal<{ id: number; message: string; type: 'success' | 'error' }[]>([]);
  readonly modal = signal<CreateModal | null>(null);
  readonly submitting = signal(false);
  readonly confirmation = signal<ConfirmationDialog | null>(null);
  readonly confirmationBusy = signal(false);
  readonly activeTab = signal<ProjectTabKind>('epics');
  readonly members = signal<ProjectMember[]>([]);
  readonly notifications = signal<Notification[]>([]);
  readonly unreadCount = signal(0);
  readonly showNotifications = signal(false);
  readonly showUserMenu = signal(false);
  readonly projectStats = signal<ProjectStats | null>(null);
  readonly schedules = signal<AgentSchedule[]>([]);
  // Epic 15: 文档维护
  readonly documents = signal<DocumentItem[]>([]);
  readonly docItem = signal<DocumentItem | null>(null);
  readonly documentComments = signal<DocumentCommentItem[]>([]);
  readonly docFilterType = signal<DocumentType | ''>('');
  readonly docFilterStatus = signal<DocumentStatus | ''>('');
  readonly docSearchQuery = signal('');
  readonly docEditing = signal(false);
  readonly docEditTitle = signal('');
  readonly docEditContent = signal('');
  readonly docEditType = signal<DocumentType>('plan');
  readonly docEditStatus = signal<DocumentStatus>('draft');
  readonly docEditEpicId = signal<number | null>(null);
  readonly docEditStoryId = signal<number | null>(null);
  readonly docCommentContent = signal('');
  readonly docCommentPreview = signal(false);
  readonly docMermaidReady = signal(false);
  readonly docDetailEpics = signal<Epic[]>([]);
  readonly docDetailStories = signal<Story[]>([]);
  private _docMermaidLoading = false;
  // 新建文档表单状态
  readonly docCreateOpen = signal(false);
  readonly docCreateProjectId = signal<number | null>(null);
  readonly docCreateEpics = signal<Epic[]>([]);
  readonly docCreateStories = signal<Story[]>([]);
  readonly docCreateEpicId = signal<number | null>(null);
  readonly docCreateStoryId = signal<number | null>(null);
  // 文档弹窗（新建 / 编辑统一）
  readonly docModal = signal<{ mode: 'create' | 'edit' } | null>(null);
  readonly docCreateTitle = signal('');
  readonly docCreateType = signal<DocumentType>('plan');
  readonly docCreateContent = signal('');
  // 计划（Sprint）创建弹窗
  readonly sprintModalOpen = signal<number | null>(null);
  readonly sprintName = signal('');
  readonly sprintType = signal<'cron' | 'once'>('cron');
  readonly sprintCron = signal('');
  // Task 编辑弹窗（替代详情页内联表单）
  readonly taskEditModal = signal<Task | null>(null);
  readonly taskEditTitle = signal('');
  readonly taskEditType = signal<ItemType>('task');
  readonly taskEditPriority = signal<Priority>('medium');
  readonly taskEditDueDate = signal<string | null>(null);
  readonly taskEditLabels = signal('');
  readonly taskEditSprintId = signal<number | null>(null);
  readonly taskEditAssigneeId = signal<number | null>(null);
  readonly taskEditDescription = signal('');
  readonly taskEditSpec = signal('');
  readonly projectListPageSize = 20;
  readonly epicsPage = signal(1);
  readonly sprintsPage = signal(1);
  readonly backlogPage = signal(1);
  readonly membersPage = signal(1);
  readonly schedulesPage = signal(1);
  readonly tabSkeletonRows = [0, 1, 2, 3, 4];
  readonly projectTabLoading = signal<Record<ProjectTabKind, boolean>>({
    epics: false,
    sprints: false,
    backlog: false,
    settings: false,
    members: false,
    stats: false,
    schedules: false,
    documents: false,
  });
  readonly projectTabLoaded = signal<Record<ProjectTabKind, boolean>>({
    epics: false,
    sprints: false,
    backlog: false,
    settings: false,
    members: false,
    stats: false,
    schedules: false,
    documents: false,
  });
  readonly projectTabErrors = signal<Record<ProjectTabKind, string>>({
    epics: '',
    sprints: '',
    backlog: '',
    settings: '',
    members: '',
    stats: '',
    schedules: '',
    documents: '',
  });
  private projectTabGeneration = 0;
  readonly statsMaxCreated = computed(() => {
    const stats = this.projectStats();
    if (!stats) return 1;
    return Math.max(...(stats.daily_created.map(d => d.count) || [1]), 1);
  });
  readonly statsMaxDone = computed(() => {
    const stats = this.projectStats();
    if (!stats) return 1;
    return Math.max(...(stats.daily_done.map(d => d.count) || [1]), 1);
  });
  readonly isOwner = signal(false);
  readonly isAdmin = signal(false);
  readonly healthStatus = signal<'ok' | 'error' | 'unknown'>('unknown');
  readonly healthDetail = signal<{ status: string; database: string; version: string; timestamp: string } | null>(null);
  readonly showHealth = signal(false);
  readonly offlineBanner = signal(false);  // Task 402: API 离线检测
  // Epic 21 Story 21.4: 离线状态详细提示
  readonly offlineQueueCount = signal(0);
  readonly appError = signal<string | null>(null);  // Task 431: 错误边界
  readonly attachments = signal<Attachment[]>([]);
  readonly adminUsers = signal<any[]>([]);
  readonly adminProjects = signal<any[]>([]);
  readonly selectedTasks = signal<Set<number>>(new Set());
  readonly bulkActionTarget = signal<string | null>(null); // 'status' | 'priority' | 'assignee' | 'due' | 'delete' | null
  readonly bulkAssigneeId = signal<number | null>(null); // v3.0 批量指派：当前选中的指派人
  readonly bulkDueDateValue = signal<string>(''); // v3.2 批量改截止日期：当前选中的日期（YYYY-MM-DD）
  // Epic 21 Story 21.3: 批量操作进度跟踪
  readonly bulkProgress = signal<{ current: number; total: number; message: string } | null>(null);
  readonly focusedTaskId = signal<number | null>(null);
  readonly exportDropdownOpen = signal(false);
  // Epic 21 Story 21.4: 组件级错误边界状态
  readonly hasError = signal(false);
  readonly errorMessage = signal('');
  readonly lastSelectedTaskId = signal<number | null>(null); // Shift+点击多选支持
  // B-04: 看板拖拽状态
  readonly dragTaskId = signal<number | null>(null);
  readonly dragOverStatus = signal<Status | null>(null);

  // Epic 26 Task 702: 搜索历史记录
  readonly searchHistory = signal<{ query: string; timestamp: number }[]>([]);
  readonly showSearchHistory = signal(false);

  // Epic 26 Task 704: 任务详情相邻导航
  readonly prevTask = signal<Task | null>(null);
  readonly nextTask = signal<Task | null>(null);
  // Epic 25: API Keys
  readonly profile = signal<UserProfile | null>(null);
  readonly myProjects = signal<Project[]>([]);
  readonly apiKeys = signal<ApiKeyInfo[]>([]);
  readonly newKeyName = signal('');
  readonly newKeyPerms = signal('');
  readonly keyModalVisible = signal(false);
  // Task 714: 虚拟滚动 - 列表分页加载（初始显示数量）
  readonly taskPageSize = signal(50);
  readonly taskPageCount = signal(1);
  // Story 任务分页（修复：Story 只显示自己的 task/bug，带分页）
  readonly storyTaskPage = signal(1);
  readonly storyTaskTotal = signal(0);
  readonly storyTaskPageSize = 50;
  // Task 716: 全局快捷键面板
  readonly showShortcuts = signal(false);
  readonly createdKeyPlaintext = signal('');
  // Epic 22: 任务依赖
  readonly taskDependencies = signal<TaskDependencies | null>(null);
  // Epic 22: Webhooks
  readonly webhooks = signal<WebhookConfig[]>([]);
  // Epic 22: 审计日志
  readonly auditLogs = signal<AuditLog[]>([]);
  // Task 708: 性能指标显示
  readonly apiMetrics = signal<ApiMetric[]>([]);
  readonly avgApiDuration = signal<number>(0);
  readonly apiSuccessRate = signal<number>(100);
  readonly pageLoadTime = signal<number>(0);
  readonly showPerformance = signal(false);
  // Task 721: 看板列折叠状态
  readonly collapsedColumns = signal<Set<string>>(new Set(
    JSON.parse(localStorage.getItem('agentboard_collapsed_cols') || '[]')
  ));
  // Task 719: 通知按类型分组
  readonly groupedNotifications = computed(() => {
    const notifs = this.notifications();
    const groups: Record<string, typeof notifs> = {};
    for (const n of notifs) {
      const key = n.type || 'other';
      if (!groups[key]) groups[key] = [];
      groups[key].push(n);
    }
    return groups;
  });
  // Task 727: 通知面板搜索过滤
  readonly notifSearchQuery = signal('');
  readonly filteredGroupedNotifications = computed(() => {
    const q = this.notifSearchQuery().toLowerCase();
    const groups = this.groupedNotifications();
    if (!q) return groups;
    const result: Record<string, any[]> = {};
    for (const [key, items] of Object.entries(groups)) {
      const filtered = items.filter((n: any) =>
        n.title?.toLowerCase().includes(q) || n.content?.toLowerCase().includes(q)
      );
      if (filtered.length > 0) result[key] = filtered;
    }
    return result;
  });
  readonly statuses: Status[] = [
    'backlog',
    'todo',
    'in_progress',
    'in_review',
    'verifying',
    'done',
  ];
  readonly priorities: Priority[] = ['highest', 'high', 'medium', 'low', 'lowest'];

  readonly visibleProjects = computed(() =>
    this.match(this.projects(), (p) => `${p.name} ${p.key || ''} ${p.description}`),
  );
  readonly visibleEpics = computed(() =>
    this.match(this.epics(), (e) => `${e.title} ${e.description}`),
  );
  readonly visibleStories = computed(() =>
    this.match(this.stories(), (s) => `${s.title} ${s.description}`),
  );
  // Task 730 / v2.6: 任务列表排序（含「按状态」排序 + 偏好持久化）
  readonly taskSortKey = signal<'created_at' | 'updated_at' | 'priority' | 'title' | 'status' | 'due_date' | 'assignee'>(
    (() => { try { return (localStorage.getItem('agentboard_sort_key') as any) || 'created_at'; } catch { return 'created_at'; } })()
  );
  readonly taskSortOrder = signal<'asc' | 'desc'>(
    (() => { try { return (localStorage.getItem('agentboard_sort_order') as 'asc' | 'desc') || 'desc'; } catch { return 'desc'; } })()
  );
  setTaskSortKey(v: string): void {
    this.taskSortKey.set(v as any);
    try { localStorage.setItem('agentboard_sort_key', v); } catch { /* ignore */ }
  }
  toggleTaskSortOrder(): void {
    const next = this.taskSortOrder() === 'asc' ? 'desc' : 'asc';
    this.taskSortOrder.set(next);
    try { localStorage.setItem('agentboard_sort_order', next); } catch { /* ignore */ }
  }
  readonly taskSortOptions = [
    { key: 'created_at', label: '创建时间' },
    { key: 'updated_at', label: '更新时间' },
    { key: 'priority', label: '优先级' },
    { key: 'title', label: '标题' },
    { key: 'status', label: '状态' },
    { key: 'due_date', label: '截止日期' },
    { key: 'assignee', label: '指派人' },
  ];
  // v3.3: 按截止日期比较（无日期按标准语义：升序置后、降序置前）
  private compareDueDate(da: string | null, db: string | null): number {
    const aNull = !da;
    const bNull = !db;
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    return new Date(da as string).getTime() - new Date(db as string).getTime();
  }
  // v3.3: 指派人的排序标签（未指派排最后）
  private assigneeSortLabel(t: Task): string {
    if (t.assignee_id == null) return '￿';
    const name = this.getAssigneeName(Number(t.assignee_id));
    return name || `u${t.assignee_id}`;
  }
  // Task 813: 搜索结果空状态
  readonly searchResultEmpty = signal(false);
  // Task 817: 快捷键导航增强 - 方向键导航状态
  readonly arrowNavIndex = signal(-1);
  readonly arrowNavItems = signal<any[]>([]);
  // Task 818: 骨架屏增强 - 加载动画状态
  readonly skeletonPulse = signal(true);
  // Task 819: 操作反馈动画
  readonly operationFeedback = signal<{ type: 'success' | 'error' | null; message: string }>({ type: null, message: '' });
  // Task 822: Story 子任务完成进度
  readonly storyTaskProgress = computed(() => {
    const total = this.tasks().length;
    const done = this.tasks().filter(t => t.status === 'done').length;
    return { total, done, pct: total > 0 ? Math.round((done / total) * 100) : 0 };
  });
  // Epic 33.1: Epic 进度可视化（Story 数 + Task 完成率）
  epicProgress(epicId: number): { stories: number; doneStories: number; tasks: number; doneTasks: number; pct: number } {
    const epicStories = this.stories().filter(s => s.epic_id === epicId);
    const storyIds = new Set(epicStories.map(s => s.id));
    const epicTasks = this.tasks().filter(t => t.story_id !== null && storyIds.has(t.story_id));
    const doneStories = epicStories.filter(s => s.status === 'done').length;
    const doneTasks = epicTasks.filter(t => t.status === 'done').length;
    const total = epicStories.length + epicTasks.length;
    const done = doneStories + doneTasks;
    return {
      stories: epicStories.length,
      doneStories,
      tasks: epicTasks.length,
      doneTasks,
      pct: total > 0 ? Math.round((done / total) * 100) : 0,
    };
  }
  // Task 602: 高级筛选面板 - 状态/优先级过滤
  // Epic 37 (v2.5): 状态快速筛选 chips —— 初始化读取持久化选择
  readonly filterStatus = signal(
    (() => { try { return localStorage.getItem('agentboard_quick_status') || ''; } catch { return ''; } })()
  );
  readonly filterPriority = signal('');
  // Task 602: 高级筛选面板 - 多选过滤
  readonly filterOpen = signal(false);
  // Task 716: 优先级快速筛选 chips —— 初始化读取持久化选择
  readonly filterPriorities = signal<string[]>(
    (() => { try { return JSON.parse(localStorage.getItem('agentboard_quick_priority') || '[]'); } catch { return []; } })()
  );
  // Epic 38 (v2.4): 任务类型快速筛选 chips —— 初始化读取持久化选择
  readonly filterTypes = signal<string[]>(
    (() => { try { return JSON.parse(localStorage.getItem('agentboard_quick_type') || '[]'); } catch { return []; } })()
  );
  // Epic 40 (v2.8): 截止日期快速筛选 chips —— 单选（''=全部）：overdue/today/week/none
  readonly filterDueDate = signal<string>(
    (() => { try { return localStorage.getItem('agentboard_quick_due') || ''; } catch { return ''; } })()
  );
  // B-01: Label filter
  readonly labelFilter = signal('');
  // Epic 33 (v2.2): 只看指派给我的任务（快速筛选）
  readonly filterMineOnly = signal<boolean>(localStorage.getItem('agentboard_filter_mine') === '1');
  // Epic 35: Task keyword search (local to story task list)
  readonly taskSearchQuery = signal('');
  // Epic 36: Inline task title editing
  readonly editingTaskId = signal<number | null>(null);
  readonly editingTaskTitle = signal('');
  readonly activeFilterCount = computed(() => this.filterPriorities().length + this.filterTypes().length + this.filterAssignees().length + (this.filterStatus() ? 1 : 0) + (this.filterDueDate() ? 1 : 0) + (this.labelFilter() ? 1 : 0) + (this.filterMineOnly() ? 1 : 0));
  // Epic 34 (v2.3): 工具条「清除全部筛选」按钮显隐 —— 搜索框非空或任一筛选活跃时显示
  readonly showClearAll = computed(() => this.taskSearchQuery().trim() !== '' || this.activeFilterCount() > 0);
  // Task 716: 优先级快速筛选 chips —— 各优先级任务计数（基于当前 story 全量任务，不受筛选影响）
  readonly priorityCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = { highest: 0, high: 0, medium: 0, low: 0, lowest: 0 };
    for (const t of this.tasks()) {
      if (t.priority in counts) counts[t.priority]++;
    }
    return counts;
  });
  // Epic 37 (v2.5): 状态快速筛选 chips —— 各状态任务计数（基于当前 story 全量任务，不受筛选影响）
  readonly statusCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = { backlog: 0, todo: 0, in_progress: 0, in_review: 0, verifying: 0, done: 0 };
    for (const t of this.tasks()) {
      if (t.status in counts) counts[t.status]++;
    }
    return counts;
  });
  // Epic 38 (v2.4): 任务类型快速筛选 chips —— 各类型任务计数（基于当前 story 全量任务，不受筛选影响）
  readonly typeCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = { task: 0, bug: 0 };
    for (const t of this.tasks()) {
      if (t.type in counts) counts[t.type]++;
    }
    return counts;
  });
  // Epic 39 (v2.7): 指派人快速筛选 chips —— 初始化读取持久化选择（user_id 列表，含 'unassigned' 哨兵）
  readonly filterAssignees = signal<string[]>(
    (() => { try { return JSON.parse(localStorage.getItem('agentboard_quick_assignee') || '[]'); } catch { return []; } })()
  );
  // v3.1: 筛选预设（保存/应用/删除当前筛选组合，纯前端 localStorage 持久化）
  readonly filterPresets = signal<FilterPreset[]>(this.loadFilterPresets());
  readonly presetName = signal('');
  readonly presetOpen = signal(false);
  private loadFilterPresets(): FilterPreset[] {
    try {
      const raw = localStorage.getItem('agentboard_filter_presets');
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    } catch { return []; }
  }
  private persistFilterPresets(): void {
    try { localStorage.setItem('agentboard_filter_presets', JSON.stringify(this.filterPresets())); } catch { /* ignore */ }
  }
  // Epic 39 (v2.7): 指派人快速筛选 chips —— 各指派人任务计数（基于当前 story 全量任务，不受筛选影响）
  readonly assigneeCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = {};
    for (const t of this.tasks()) {
      const key = t.assignee_id != null ? String(t.assignee_id) : 'unassigned';
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  });
  // Epic 39 (v2.7): 渲染用指派人 chips（按计数降序，仅展示 count>0 的指派人 + 未指派）
  readonly assigneeChipList = computed<{ key: string; label: string; initials: string; count: number }[]>(() => {
    const counts = this.assigneeCounts();
    const keys = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
    const out: { key: string; label: string; initials: string; count: number }[] = [];
    for (const k of keys) {
      if (k === 'unassigned') {
        out.push({ key: k, label: '未指派', initials: '?', count: counts[k] });
      } else {
        const id = Number(k);
        out.push({ key: k, label: this.getAssigneeName(id) || `用户${id}`, initials: this.getAssigneeInitials(id) || '?', count: counts[k] });
      }
    }
    return out;
  });
  // Epic 40 (v2.8): 截止日期快速筛选 chips —— 各日期分桶任务计数（基于当前 story 全量任务，不受筛选影响）
  // 分桶：overdue(已逾期且未完成) / today(今天到期) / week(未来 1~7 天到期) / none(无截止日期)
  readonly dueCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = { overdue: 0, today: 0, week: 0, none: 0 };
    for (const t of this.tasks()) {
      const b = this.dueBucket(t);
      if (b === 'overdue' && t.status === 'done') continue; // 逾期桶不含已完成
      if (b in counts) counts[b]++;
    }
    return counts;
  });
  readonly allLabels = computed(() => {
    const set = new Set<string>();
    for (const t of this.tasks()) {
      for (const l of this.parseLabels(t.labels)) set.add(l);
    }
    return [...set].sort();
  });
  readonly visibleTasks = computed(() => {
    const search = this.match(this.tasks(), (t) => `${t.title} ${t.description} ${t.spec}`);
    const status = this.filterStatus();
    const priority = this.filterPriority();
    const sortKey = this.taskSortKey();
    const sortOrder = this.taskSortOrder();
    const PRIORITY_ORDER = ['highest', 'high', 'medium', 'low', 'lowest'];
    let filtered = search.filter((t: Task) => {
      if (status && t.status !== status) return false;
      if (priority && t.priority !== priority) return false;
      const fp = this.filterPriorities();
      if (fp.length && !fp.includes(t.priority)) return false;
      const ft = this.filterTypes();
      if (ft.length && !ft.includes(t.type)) return false;
      // Epic 39 (v2.7): 指派人快速筛选 chips —— 单选指派人（含未指派哨兵）时过滤
      const fa = this.filterAssignees();
      if (fa.length) {
        const key = t.assignee_id != null ? String(t.assignee_id) : 'unassigned';
        if (!fa.includes(key)) return false;
      }
      // Epic 40 (v2.8): 截止日期快速筛选 chips —— 单选分桶（overdue/today/week/none）
      const fd = this.filterDueDate();
      if (fd) {
        const b = this.dueBucket(t);
        const overdueDone = b === 'overdue' && t.status === 'done';
        if (overdueDone || b !== fd) return false;
      }
      // B-01: Label filter
      const lf = this.labelFilter();
      if (lf && !this.parseLabels(t.labels).includes(lf)) return false;
      // Epic 35: Local task keyword search (title + description, case-insensitive)
      const tq = this.taskSearchQuery().trim().toLocaleLowerCase();
      if (tq && !(`${t.title} ${t.description}`.toLocaleLowerCase().includes(tq))) return false;
      // Epic 33 (v2.2): 只看指派给我的任务（成员已加载且命中当前用户时生效，否则无操作）
      if (this.filterMineOnly()) {
        const myId = this.myUserId();
        if (myId != null && this.members().length > 0 && t.assignee_id !== myId) return false;
      }
      return true;
    });
    // Task 730 / v2.6: 排序（含按状态工作流顺序）
    filtered.sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'created_at' || sortKey === 'updated_at') {
        cmp = new Date(a[sortKey]).getTime() - new Date(b[sortKey]).getTime();
      } else if (sortKey === 'priority') {
        cmp = PRIORITY_ORDER.indexOf(a.priority) - PRIORITY_ORDER.indexOf(b.priority);
      } else if (sortKey === 'title') {
        cmp = (a.title || '').localeCompare(b.title || '');
      } else if (sortKey === 'status') {
        cmp = this.statuses.indexOf(a.status) - this.statuses.indexOf(b.status);
      } else if (sortKey === 'due_date') {
        // v3.3: 按截止日期排序（无截止日期按标准语义：升序置后、降序置前）
        cmp = this.compareDueDate(a.due_date, b.due_date);
      } else if (sortKey === 'assignee') {
        // v3.3: 按指派人排序（未指派置后）
        cmp = this.assigneeSortLabel(a).localeCompare(this.assigneeSortLabel(b));
      }
      return sortOrder === 'asc' ? cmp : -cmp;
    });
    return filtered;
  });
  // Task 836: 任务列表分组（不分组 / 按状态 / 按类型 / 按负责人）
  readonly taskGroupBy = signal<'none' | 'status' | 'type' | 'assignee'>(
    (localStorage.getItem('agentboard_story_group') as 'none' | 'status' | 'type' | 'assignee') || 'none'
  );
  readonly taskGroupOptions = [
    { key: 'none', label: '不分组' },
    { key: 'status', label: '按状态' },
    { key: 'type', label: '按类型' },
    { key: 'assignee', label: '按负责人' },
  ];
  setTaskGroup(v: string): void {
    this.taskGroupBy.set(v as any);
    localStorage.setItem('agentboard_story_group', v);
  }
  private groupLabel(mode: string, key: string): string {
    if (mode === 'status') return this.statusLabel(key);
    if (mode === 'type') return key === 'bug' ? 'Bug' : '任务';
    if (key === '' || key === 'unassigned') return '未指派';
    return this.getAssigneeName(Number(key)) || '未指派';
  }
  readonly groupedTasks = computed(() => {
    const g = this.taskGroupBy();
    const list = this.visibleTasks();
    if (!list.length) return [] as { key: string; label: string; count: number; items: Task[] }[];
    if (g === 'none') return [{ key: '', label: '', count: list.length, items: list }];
    const buckets: Record<string, Task[]> = {};
    for (const t of list) {
      const k =
        g === 'status'
          ? t.status
          : g === 'type'
            ? t.type
            : t.assignee_id == null
              ? 'unassigned'
              : String(t.assignee_id);
      (buckets[k] ||= []).push(t);
    }
    let keys: string[];
    if (g === 'status') keys = this.statuses.filter((s) => buckets[s]);
    else if (g === 'type') keys = ['task', 'bug'].filter((k) => buckets[k]);
    else keys = Object.keys(buckets).sort((a, b) =>
      this.groupLabel('assignee', a).localeCompare(this.groupLabel('assignee', b), 'zh'));
    return keys.map((k) => ({ key: k, label: this.groupLabel(g, k), count: buckets[k].length, items: buckets[k] }));
  });
  // v1.8: Collapsible task groups — persist collapsed keys in localStorage
  readonly collapsedGroups = signal<Set<string>>(
    new Set(JSON.parse(localStorage.getItem('agentboard_collapsed_groups') || '[]'))
  );
  isGroupCollapsed(key: string): boolean { return this.collapsedGroups().has(key); }
  toggleGroup(key: string): void {
    const s = new Set(this.collapsedGroups());
    if (s.has(key)) s.delete(key); else s.add(key);
    this.collapsedGroups.set(s);
    localStorage.setItem('agentboard_collapsed_groups', JSON.stringify([...s]));
  }
  // v1.9: 分组一键全折叠 / 全展开（互补 v1.8 单组折叠）
  readonly allGroupsCollapsed = computed(() => {
    if (this.taskGroupBy() === 'none') return false;
    const groups = this.groupedTasks();
    if (!groups.length) return false;
    return groups.every((g) => !!g.key && this.collapsedGroups().has(g.key));
  });
  collapseAllGroups(): void {
    const s = new Set(this.groupedTasks().map((g) => g.key).filter((k) => !!k));
    this.collapsedGroups.set(s);
    localStorage.setItem('agentboard_collapsed_groups', JSON.stringify([...s]));
  }
  expandAllGroups(): void {
    this.collapsedGroups.set(new Set<string>());
    localStorage.setItem('agentboard_collapsed_groups', JSON.stringify([]));
  }
  readonly doneTasks = computed(() => this.tasks().filter((t) => t.status === 'done').length);
  // Epic 34.1: 任务列表汇总栏（总数/完成率/状态分布堆叠条）
  readonly taskListSummary = computed(() => {
    const list = this.tasks();
    const total = list.length;
    const done = list.filter((t) => t.status === 'done').length;
    const inProgress = list.filter((t) => t.status === 'in_progress' || t.status === 'in_review' || t.status === 'verifying').length;
    const rate = total === 0 ? 0 : Math.round((done / total) * 100);
    const segments = this.statuses
      .map((st) => ({ status: st, count: list.filter((t) => t.status === st).length }))
      .filter((seg) => seg.count > 0);
    return { total, done, inProgress, rate, segments };
  });

  private routeSub?: Subscription;
  private toastTimer?: ReturnType<typeof setTimeout>;
  private healthTimer?: ReturnType<typeof setInterval>;   // Task 400: 健康检查轮询
  private notifTimer?: ReturnType<typeof setInterval>;    // Task 401: 通知轮询
  private readonly colorScheme = window.matchMedia?.('(prefers-color-scheme: dark)');
  private readonly handleColorSchemeChange = (event: MediaQueryListEvent): void => {
    if (!localStorage.getItem('agentboard_theme')) {
      this.applyTheme(event.matches ? 'dark' : 'light');
    }
  };
  private readonly handleAuthExpired = (): void => {
    this.currentUser.set('');
    this.isAdmin.set(false);
    this.isOwner.set(false);
    this.showLogin();
    this.notify('登录已失效，请重新登录', 'error');
  };
  // Task 402: 网络离线检测
  // Epic 21 Story 21.4: 优化离线状态提示
  private readonly handleOnline = (): void => {
    this.offlineBanner.set(false);
    this.offlineQueueCount.set(0);
    // Task 472: flush offline queue when back online
    const queue = (() => {
      try { return JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]'); }
      catch { return []; }
    })();
    if (queue.length > 0) {
      localStorage.removeItem('agentboard_offline_queue');
      this.notify(`已恢复网络，正在重发 ${queue.length} 个离线操作…`);
    }
  };
  private readonly handleOffline = (): void => {
    this.offlineBanner.set(true);
    // 同步更新离线队列计数
    try {
      const queue = JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]');
      this.offlineQueueCount.set(queue.length);
    } catch { this.offlineQueueCount.set(0); }
  };

  // Epic 21 Story 21.4: 全局错误边界处理器
  private readonly handleGlobalError = (event: ErrorEvent): void => {
    const msg = event.message || '发生了未知错误';
    // 忽略某些常见的非关键错误
    if (msg.includes('ResizeObserver') || msg.includes('ResizeObserver loop')) return;
    this.appError.set(msg);
    // 5 秒后自动消失（除非是严重错误）
    setTimeout(() => {
      if (this.appError() === msg) this.appError.set(null);
    }, 5000);
  };

  private readonly handleUnhandledRejection = (event: PromiseRejectionEvent): void => {
    // 忽略离线队列相关错误
    if (event.reason?.message?.includes('离线')) {
      return;
    }
    const msg = event.reason?.message || '异步错误';
    this.appError.set(msg);
    setTimeout(() => {
      if (this.appError() === msg) this.appError.set(null);
    }, 5000);
    console.error('[UnhandledRejection]', event.reason);
  };

  // Story 21.4: 错误边界重置
  resetErrorBoundary(): void {
    this.hasError.set(false);
    this.errorMessage.set('');
    this.appError.set(null);
  }

  constructor(
    readonly api: ApiService,
    private readonly router: Router,
    @Inject(DOCUMENT) private readonly document: Document,
  ) {}

  ngOnInit(): void {
    // Task 708: 记录页面加载时间
    this.pageLoadTime.set(performance.now());
    window.addEventListener(AUTH_EXPIRED_EVENT, this.handleAuthExpired);
    window.addEventListener('online', this.handleOnline);    // Task 402: 离线检测
    window.addEventListener('offline', this.handleOffline);
    window.addEventListener('error', this.handleGlobalError); // Task 431: 错误边界
    const saved = localStorage.getItem('agentboard_theme');
    // 优先使用用户偏好，其次跟随系统
    const theme = saved || (this.colorScheme?.matches ? 'dark' : 'light');
    this.applyTheme(theme);
    this.loadRecentProjects();
    this.loadFavorites();
    // Listen for system theme changes
    this.colorScheme?.addEventListener('change', this.handleColorSchemeChange);
    // Epic 21 Story 21.4: 全局错误处理
    window.addEventListener('error', this.handleGlobalError);
    window.addEventListener('unhandledrejection', this.handleUnhandledRejection);
    // 启动时校验已有 token，失败则清除并显示登录
    void this.validateAuth();
    this.routeSub = this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.loadRoute());
    void this.loadRoute();
    // Task 400: 健康检查轮询（默认开启，可通过 localStorage 关闭）
    if (localStorage.getItem('agentboard_health_poll') !== 'disabled') {
      void this.checkHealth();
      this.healthTimer = setInterval(() => {
        if (localStorage.getItem('agentboard_health_poll') !== 'disabled') {
          void this.checkHealth();
        }
      }, 60000); // 60s
    }
    // Task 401: 通知轮询（每 60s）
    this.notifTimer = setInterval(() => {
      if (this.authVisible()) return;
      void this.loadNotifications();
    }, 60000);
    // Story 21.4: 初始化时更新离线队列计数
    try {
      const queue = JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]');
      this.offlineQueueCount.set(queue.length);
    } catch { this.offlineQueueCount.set(0); }
    // Epic 26 Task 702: 加载搜索历史记录
    this.loadSearchHistory();
    // Task 716/711/815/817: 全局快捷键 - '?' 键打开快捷键帮助，Ctrl+A 全选，Del 删除选中，/ 聚焦搜索，←→ 导航
    window.addEventListener('keydown', (e: KeyboardEvent) => {
      if (this.confirmation()) {
        if (e.key === 'Escape') {
          e.preventDefault();
          this.cancelConfirmation();
        }
        return;
      }
      if (this.isInputFocused()) return;
      if (e.key === '?') {
        e.preventDefault();
        this.toggleShortcuts();
      }
      // Task 711: Ctrl+A 全选当前列表任务
      if (e.ctrlKey && e.key === 'a') {
        e.preventDefault();
        this.selectAllTasks();
      }
      // Task 711: Del 删除选中任务
      if (e.key === 'Delete' && this.selectedTasks().size > 0) {
        e.preventDefault();
        this.bulkDelete();
      }
      // Task 815: '/' 快捷键聚焦搜索框
      if (e.key === '/') {
        e.preventDefault();
        const searchInput = document.getElementById('global-search') as HTMLInputElement;
        if (searchInput) searchInput.focus();
      }
      // Task 817: ←→ 方向键导航列表
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        this.handleArrowNav(e.key === 'ArrowLeft' ? -1 : 1);
      }
      // Task 817: Enter 键确认导航选择
      if (e.key === 'Enter' && this.arrowNavIndex() >= 0) {
        e.preventDefault();
        this.confirmArrowNav();
      }
      // Task 605: 任务详情页快捷键 c/d/x
      if (this.task() && (e.key === 'c' || e.key === 'd' || e.key === 'x')) {
        e.preventDefault();
        if (e.key === 'c') this.quickAdvanceStatus();
        else if (e.key === 'd') this.quickCompleteTask();
        else if (e.key === 'x') this.quickDeleteTask();
      }
    });
  }

  // Task 716: 判断当前焦点是否在输入元素上
  private isInputFocused(): boolean {
    const el = document.activeElement;
    return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
  }

  // Epic 26 Task 702: 加载搜索历史记录
  private loadSearchHistory(): void {
    try {
      const stored = localStorage.getItem('agentboard_search_history');
      if (stored) {
        this.searchHistory.set(JSON.parse(stored));
      }
    } catch { this.searchHistory.set([]); }
  }

  // Epic 26 Task 702: 保存搜索历史记录
  saveSearchHistory(query: string): void {
    if (!query.trim()) return;
    try {
      const KEY = 'agentboard_search_history';
      const MAX = 10;
      let history = this.searchHistory();
      // Remove duplicate if exists
      history = history.filter(h => h.query !== query);
      // Add new query at the beginning
      history.unshift({ query, timestamp: Date.now() });
      // Keep only MAX items
      history = history.slice(0, MAX);
      this.searchHistory.set(history);
      localStorage.setItem(KEY, JSON.stringify(history));
    } catch { /* ignore */ }
  }

  // Epic 26 Task 702: 清除单条搜索历史
  removeSearchHistoryItem(query: string): void {
    try {
      const history = this.searchHistory().filter(h => h.query !== query);
      this.searchHistory.set(history);
      localStorage.setItem('agentboard_search_history', JSON.stringify(history));
    } catch { /* ignore */ }
  }

  // Epic 26 Task 702: 清除所有搜索历史
  clearSearchHistory(): void {
    this.searchHistory.set([]);
    localStorage.removeItem('agentboard_search_history');
    this.showSearchHistory.set(false);
  }

  // Epic 26 Task 702: 选择历史记录项
  selectSearchHistory(query: string): void {
    this.search.set(query);
    this.showSearchHistory.set(false);
  }

  // Task 817: 方向键导航处理
  handleArrowNav(direction: -1 | 1): void {
    const items = this.arrowNavItems();
    if (items.length === 0) return;
    let idx = this.arrowNavIndex();
    idx = idx + direction;
    if (idx < 0) idx = items.length - 1;
    if (idx >= items.length) idx = 0;
    this.arrowNavIndex.set(idx);
  }

  // Task 817: 确认导航选择
  confirmArrowNav(): void {
    const idx = this.arrowNavIndex();
    const items = this.arrowNavItems();
    if (idx >= 0 && idx < items.length) {
      const item = items[idx];
      if (item.id) {
        void this.router.navigate([`/task`, item.id]);
      }
      this.arrowNavIndex.set(-1);
      this.arrowNavItems.set([]);
    }
  }

  // Task 819: 显示操作反馈动画
  showFeedback(type: 'success' | 'error', message: string): void {
    this.operationFeedback.set({ type, message });
    setTimeout(() => {
      this.operationFeedback.set({ type: null, message: '' });
    }, 3000);
  }

  // Task 814: 清除单条搜索历史（带确认）
  clearSearchHistoryItem(query: string, event: Event): void {
    event.stopPropagation();
    event.preventDefault();
    this.removeSearchHistoryItem(query);
  }

  // Epic 26 Task 703: 高亮搜索关键词
  highlightSearch(text: string, query: string): string {
    if (!query.trim() || !text) return text;
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escaped})`, 'gi');
    return text.replace(regex, '<mark class="search-highlight">$1</mark>');
  }

  // Epic 26 Task 704: 计算相邻任务
  private updatePrevNextTasks(currentTaskId: number): void {
    const allTasks = this.visibleTasks();
    const currentIndex = allTasks.findIndex(t => t.id === currentTaskId);
    if (currentIndex >= 0) {
      this.prevTask.set(currentIndex > 0 ? allTasks[currentIndex - 1] : null);
      this.nextTask.set(currentIndex < allTasks.length - 1 ? allTasks[currentIndex + 1] : null);
    }
  }

  async checkHealth(): Promise<void> {
    try {
      const health = await firstValueFrom(this.api.getHealth());
      this.healthStatus.set(health.status === 'ok' && health.database === 'ok' ? 'ok' : 'error');
      this.healthDetail.set(health);
    } catch {
      this.healthStatus.set('error');
      this.healthDetail.set(null);
    }
  }

  toggleHealth(): void {
    this.showHealth.set(!this.showHealth());
    if (this.showHealth()) {
      void this.checkHealth();
      // Task 708: 更新性能指标
      this.updatePerformanceMetrics();
    }
  }

  // Task 708: 更新性能指标
  updatePerformanceMetrics(): void {
    this.apiMetrics.set(perfTracker.getRecentMetrics(10));
    this.avgApiDuration.set(Math.round(perfTracker.getAverageDuration()));
    this.apiSuccessRate.set(Math.round(perfTracker.getSuccessRate()));
  }

  // Task 708: 格式化性能指标时间
  formatMetricTime(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  // Task 708: 格式化页面加载时间
  formatLoadTime(): string {
    const ms = this.pageLoadTime();
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  // Task 400: 切换健康检查轮询
  toggleHealthPoll(): void {
    const current = localStorage.getItem('agentboard_health_poll');
    if (current === 'disabled') {
      localStorage.removeItem('agentboard_health_poll');
      this.notify('健康检查轮询已开启（每 60s）');
    } else {
      localStorage.setItem('agentboard_health_poll', 'disabled');
      this.notify('健康检查轮询已关闭');
    }
  }

  isHealthPollEnabled(): boolean {
    return localStorage.getItem('agentboard_health_poll') !== 'disabled';
  }

  /** 启动时验证 localStorage 中的 token，有效则恢复登录态，无效则清除并进入登录页 */
  private async validateAuth(): Promise<void> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) {
      this.showLogin();
      return;
    }
    try {
      const me = await firstValueFrom(this.api.me());
      this.currentUser.set(me.username);
      this.isAdmin.set(me.is_admin ?? false);
      localStorage.setItem('agentboard_user', me.username);
      localStorage.setItem('agentboard_is_admin', String(me.is_admin ?? false));
    } catch {
      // token 失效，清除并显示登录
      localStorage.removeItem('agentboard_token');
      localStorage.removeItem('agentboard_user');
      localStorage.removeItem('agentboard_is_admin');
      this.currentUser.set('');
      this.isAdmin.set(false);
      this.openAuth('login');
    }
  }

  // Task 431: 手动关闭错误提示
  dismissError(): void {
    this.appError.set(null);
  }

  ngOnDestroy(): void {
    window.removeEventListener(AUTH_EXPIRED_EVENT, this.handleAuthExpired);
    window.removeEventListener('online', this.handleOnline);    // Task 402
    window.removeEventListener('offline', this.handleOffline);
    window.removeEventListener('error', this.handleGlobalError);
    window.removeEventListener('unhandledrejection', this.handleUnhandledRejection);
    this.colorScheme?.removeEventListener('change', this.handleColorSchemeChange);
    this.routeSub?.unsubscribe();
    if (this.toastTimer) clearTimeout(this.toastTimer);
    if (this.healthTimer) clearInterval(this.healthTimer);    // Task 400
    if (this.notifTimer) clearInterval(this.notifTimer);     // Task 401
  }

  private match<T>(items: T[], text: (item: T) => string): T[] {
    const query = this.search().trim().toLocaleLowerCase();
    return query ? items.filter((item) => text(item).toLocaleLowerCase().includes(query)) : items;
  }

  paginatedItems<T>(items: T[], page: number): T[] {
    const totalPages = Math.max(1, Math.ceil(items.length / this.projectListPageSize));
    const currentPage = Math.min(Math.max(1, page), totalPages);
    const start = (currentPage - 1) * this.projectListPageSize;
    return items.slice(start, start + this.projectListPageSize);
  }

  setProjectListPage(kind: ProjectListKind, page: number): void {
    if (kind === 'epics') this.epicsPage.set(page);
    else if (kind === 'sprints') this.sprintsPage.set(page);
    else if (kind === 'backlog') this.backlogPage.set(page);
    else if (kind === 'members') this.membersPage.set(page);
    else this.schedulesPage.set(page);

    setTimeout(() => {
      this.document.getElementById(`${kind}-list`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  private resetProjectListPages(): void {
    this.epicsPage.set(1);
    this.sprintsPage.set(1);
    this.backlogPage.set(1);
    this.membersPage.set(1);
    this.schedulesPage.set(1);
  }

  selectProjectTab(tab: ProjectTabKind): void {
    this.activeTab.set(tab);
    const projectId = this.project()?.id;
    if (projectId) void this.loadProjectTab(tab, projectId);
  }

  isProjectTabLoading(tab: ProjectTabKind): boolean {
    return this.projectTabLoading()[tab];
  }

  isProjectTabLoaded(tab: ProjectTabKind): boolean {
    return this.projectTabLoaded()[tab];
  }

  projectTabError(tab: ProjectTabKind): string {
    return this.projectTabErrors()[tab];
  }

  retryProjectTab(tab: ProjectTabKind): void {
    const projectId = this.project()?.id;
    if (!projectId) return;
    this.projectTabLoaded.update((state) => ({ ...state, [tab]: false }));
    void this.loadProjectTab(tab, projectId, true);
  }

  private setProjectTabLoading(tab: ProjectTabKind, loading: boolean): void {
    this.projectTabLoading.update((state) => ({ ...state, [tab]: loading }));
  }

  private resetProjectTabs(): void {
    this.projectTabGeneration += 1;
    this.epics.set([]);
    this.stories.set([]);
    this.tasks.set([]);
    this.sprints.set([]);
    this.backlogTasks.set([]);
    this.members.set([]);
    this.projectStats.set(null);
    this.schedules.set([]);
    this.documents.set([]);
    this.isOwner.set(false);
    this.projectTabLoading.set({
      epics: false,
      sprints: false,
      backlog: false,
      settings: false,
      members: false,
      stats: false,
      schedules: false,
      documents: false,
    });
    this.projectTabLoaded.set({
      epics: false,
      sprints: false,
      backlog: false,
      settings: false,
      members: false,
      stats: false,
      schedules: false,
      documents: false,
    });
    this.projectTabErrors.set({
      epics: '',
      sprints: '',
      backlog: '',
      settings: '',
      members: '',
      stats: '',
      schedules: '',
      documents: '',
    });
  }

  private async loadProjectTab(tab: ProjectTabKind, projectId: number, force = false): Promise<void> {
    if (!force && (this.isProjectTabLoading(tab) || this.isProjectTabLoaded(tab))) return;

    const generation = this.projectTabGeneration;
    if (tab === 'settings') {
      await this.loadProjectAccess(projectId, generation);
      return;
    }
    this.setProjectTabLoading(tab, true);
    this.projectTabErrors.update((state) => ({ ...state, [tab]: '' }));

    try {
      if (tab === 'epics') {
        const epics = await firstValueFrom(this.api.listEpics(projectId));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.epics.set(epics);
        void this.loadEpicProgressData(projectId, epics, generation);
      } else if (tab === 'sprints') {
        const sprints = await firstValueFrom(this.api.listSprints(projectId));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.sprints.set(sprints);
      } else if (tab === 'backlog') {
        const tasks = await firstValueFrom(this.api.searchTasks({ project_id: projectId, limit: 200 }));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.backlogTasks.set(tasks.filter((task) => !task.sprint_id));
      } else if (tab === 'members') {
        const [members, me] = await Promise.all([
          firstValueFrom(this.api.listMembers(projectId)),
          firstValueFrom(this.api.me()),
        ]);
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.members.set(members.items);
        this.applyProjectAccess(me, members.items);
        this.projectTabLoaded.update((state) => ({ ...state, settings: true }));
      } else if (tab === 'stats') {
        const stats = await firstValueFrom(this.api.getProjectStats(projectId));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.projectStats.set(stats);
      } else if (tab === 'schedules') {
        const schedules = await firstValueFrom(this.api.listSchedules(projectId));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.schedules.set(schedules);
      } else if (tab === 'documents') {
        const docs = await firstValueFrom(this.api.listDocuments({ project_id: projectId }));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.documents.set(docs || []);
      }

      this.projectTabLoaded.update((state) => ({ ...state, [tab]: true }));
    } catch (error) {
      if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
      this.projectTabErrors.update((state) => ({ ...state, [tab]: this.message(error) }));
    } finally {
      if (this.isCurrentProjectTabRequest(projectId, generation)) this.setProjectTabLoading(tab, false);
    }
  }

  private async loadProjectAccess(projectId: number, generation: number): Promise<void> {
    if (this.isProjectTabLoaded('settings') || this.isProjectTabLoading('settings')) return;
    this.setProjectTabLoading('settings', true);
    this.projectTabErrors.update((state) => ({ ...state, settings: '' }));
    try {
      const [me, members] = await Promise.all([
        firstValueFrom(this.api.me()),
        firstValueFrom(this.api.listMembers(projectId)),
      ]);
      if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
      this.applyProjectAccess(me, members.items);
      this.projectTabLoaded.update((state) => ({ ...state, settings: true }));
    } catch (error) {
      if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
      this.projectTabErrors.update((state) => ({ ...state, settings: this.message(error) }));
    } finally {
      if (this.isCurrentProjectTabRequest(projectId, generation)) this.setProjectTabLoading('settings', false);
    }
  }

  private applyProjectAccess(me: UserProfile, members: ProjectMember[]): void {
    this.isAdmin.set(me.is_admin ?? false);
    const membership = members.find((member) => member.user_id === me.id);
    this.isOwner.set(membership?.role === 'owner');
  }

  private async loadEpicProgressData(projectId: number, epics: Epic[], generation: number): Promise<void> {
    try {
      const stories = (
        await Promise.all(epics.map((epic) => firstValueFrom(this.api.listStories(epic.id))))
      ).flat();
      if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
      this.stories.set(stories);
      const tasks = (
        await Promise.all(stories.map((story) => firstValueFrom(this.api.listTasks(story.id))))
      ).flat();
      // Story 视图使用 loadStoryTasks 独立加载自身任务；非 story 视图才写入全局 tasks()
      if (this.isCurrentProjectTabRequest(projectId, generation) && this.view() !== 'story') {
        this.tasks.set(tasks);
      }
    } catch {
      // Epic 列表已经可用；进度数据加载失败不阻塞主列表。
    }
  }

  private isCurrentProjectTabRequest(projectId: number, generation: number): boolean {
    return this.project()?.id === projectId && this.projectTabGeneration === generation;
  }

  private async loadRoute(): Promise<void> {
    // 未登录时不加载任何业务数据，由独立登录页接管
    if (this.authVisible()) return;
    this.loading.set(true);
    this.error.set('');
    const path = this.router.url.split('?')[0].replace(/^\//, '');
    const [kind = '', rawId] = path.split('/');
    const id = Number(rawId);
    // 已登录用户直接访问 /login 时回首页
    if (kind === 'login') {
      await this.router.navigateByUrl('/');
      return;
    }
    try {
      await this.loadProjects();
      this.syncRecentProjects();
      this.syncFavorites();
      if (!kind) {
        this.view.set('home');
        await this.loadDashboard();
      } else if (kind === 'projects') {
        this.view.set('projects');
      } else if (kind === 'project' && id > 0) {
        this.view.set('project');
        this.activeTab.set('epics');
        this.resetProjectListPages();
        this.resetProjectTabs();
        const project = await firstValueFrom(this.api.getProject(id));
        this.project.set(project);
        this.trackRecentProject(project);
        void this.loadProjectTab('epics', id);
      } else if (kind === 'epic' && id > 0) {
        this.view.set('epic');
        const [epic, stories] = await Promise.all([
          firstValueFrom(this.api.getEpic(id)),
          firstValueFrom(this.api.listStories(id)),
        ]);
        this.epic.set(epic);
        this.stories.set(stories);
        this.project.set(await firstValueFrom(this.api.getProject(epic.project_id)));
      } else if (kind === 'story' && id > 0) {
        this.view.set('story');
        this.storyTaskPage.set(1);
        const story = await firstValueFrom(this.api.getStory(id));
        this.story.set(story);
        // 分页加载 story 任务，确保只属于当前 story
        await this.loadStoryTasks(id, 1);
        const epic = await firstValueFrom(this.api.getEpic(story.epic_id));
        this.epic.set(epic);
        this.project.set(await firstValueFrom(this.api.getProject(epic.project_id)));
        // B-02: 负责人下拉依赖成员列表，进入 Story 视图时必须加载
        await this.loadMembers(epic.project_id);
      } else if (kind === 'task' && id > 0) {
        this.view.set('task');
        const [task, comments] = await Promise.all([
          firstValueFrom(this.api.getTask(id)),
          firstValueFrom(this.api.listComments(id)),
        ]);
        this.task.set(task);
        this.comments.set(comments);
        await this.loadAttachments(id);
        if (task.story_id) {
          const story = await firstValueFrom(this.api.getStory(task.story_id));
          this.story.set(story);
          const epic = await firstValueFrom(this.api.getEpic(story.epic_id));
          this.epic.set(epic);
          const project = await firstValueFrom(this.api.getProject(epic.project_id));
          this.project.set(project);
          await this.loadSprints(project.id);
          // B-02: 任务详情改派需要成员列表
          await this.loadMembers(project.id);
        } else {
          this.project.set(await firstValueFrom(this.api.getProject(task.project_id)));
          await this.loadSprints(task.project_id);
          await this.loadMembers(task.project_id);
        }
        // Epic 26 Task 704: 更新相邻任务导航
        this.updatePrevNextTasks(id);
      } else if (kind === 'sprint' && id > 0) {
        this.view.set('sprint');
        const [sprint, tasks] = await Promise.all([
          firstValueFrom(this.api.getSprint(id)),
          firstValueFrom(this.api.listSprintTasks(id)),
        ]);
        this.sprint.set(sprint);
        this.sprintTasks.set(tasks);
        this.project.set(await firstValueFrom(this.api.getProject(sprint.project_id)));
        await this.loadSprintBurndown(id);
      } else if (kind === 'admin') {
        const me = await this.adminMe();
        if (!me?.is_admin) {
          this.router.navigateByUrl('/');
          return;
        }
        this.view.set('admin');
        await this.loadAdminData();
      } else if (kind === 'settings') {
        if (!localStorage.getItem('agentboard_token')) {
          this.router.navigateByUrl('/login');
          return;
        }
        this.view.set('settings');
        await Promise.all([this.loadProfile(), this.loadMyProjects(), this.loadApiKeys()]);
      } else if (kind === 'documents') {
        if (id > 0) {
          this.view.set('document');
          const [doc, comments] = await Promise.all([
            firstValueFrom(this.api.getDocument(id)),
            firstValueFrom(this.api.listDocumentComments(id)),
          ]);
          this.docItem.set(doc);
          this.documentComments.set(comments);
          this.docEditTitle.set(doc.title);
          this.docEditContent.set(doc.content);
          this.docEditType.set(doc.type);
          this.docEditStatus.set(doc.status);
          this.docEditEpicId.set(doc.epic_id);
          this.docEditStoryId.set(doc.story_id);
          this.docEditing.set(false);
          this.project.set(await firstValueFrom(this.api.getProject(doc.project_id)));
          const eps = await firstValueFrom(this.api.listEpics(doc.project_id));
          this.docDetailEpics.set(eps);
          if (doc.epic_id) {
            this.docDetailStories.set(await firstValueFrom(this.api.listStories(doc.epic_id)));
          } else {
            this.docDetailStories.set([]);
          }
          setTimeout(() => this.enhanceMermaid(), 80);
        } else {
          this.view.set('documents');
          await this.loadDocuments();
        }
      } else {
        this.view.set('not-found');
      }
    } catch (error) {
      const status = (error as Error & { status?: number })?.status;
      // 403（无权访问） / 404（项目不存在）→ toast 提示并回首页
      if (status === 403 || status === 404) {
        this.notify(`访问受限：${this.message(error)}`, 'error');
        await this.router.navigateByUrl('/');
        return;
      }
      this.error.set(this.message(error));
    } finally {
      this.loading.set(false);
    }
  }

  private async loadProjects(): Promise<void> {
    const result = await firstValueFrom(this.api.listProjects());
    this.projects.set(Array.isArray(result) ? result : (result.items || []));
  }

  private loadRecentProjects(): void {
    try {
      const stored = localStorage.getItem('agentboard_recent_projects');
      if (stored) {
        this.recentProjectIds = JSON.parse(stored);
      }
    } catch { /* ignore */ }
  }

  /** Populate recentProjects signal from stored IDs + loaded projects list */
  private syncRecentProjects(): void {
    if (this.recentProjectIds.length === 0) return;
    const recent = this.recentProjectIds
      .map(id => this.projects().find(p => p.id === id))
      .filter(Boolean) as Project[];
    this.recentProjects.set(recent);
  }

  /** Load favorite project IDs from localStorage */
  private loadFavorites(): void {
    try {
      const stored = localStorage.getItem('agentboard_favorite_projects');
      if (stored) {
        this.favoriteProjectIds = new Set(JSON.parse(stored));
      }
    } catch { /* ignore */ }
  }

  /** Populate favoriteProjects signal from stored IDs + loaded projects list */
  private syncFavorites(): void {
    if (this.favoriteProjectIds.size === 0) {
      this.favoriteProjects.set([]);
      return;
    }
    const favs = this.projects().filter(p => this.favoriteProjectIds.has(p.id));
    this.favoriteProjects.set(favs);
  }

  /** Toggle favorite status for a project */
  toggleFavorite(project: Project, event?: Event): void {
    event?.preventDefault();
    event?.stopPropagation();
    if (this.favoriteProjectIds.has(project.id)) {
      this.favoriteProjectIds.delete(project.id);
    } else {
      this.favoriteProjectIds.add(project.id);
    }
    localStorage.setItem('agentboard_favorite_projects', JSON.stringify([...this.favoriteProjectIds]));
    this.syncFavorites();
  }

  /** Check if a project is favorited */
  isFavorite(projectId: number): boolean {
    return this.favoriteProjectIds.has(projectId);
  }

  trackRecentProject(project: Project): void {
    try {
      const KEY = 'agentboard_recent_projects';
      const MAX = 5;
      let ids: number[] = [];
      const stored = localStorage.getItem(KEY);
      if (stored) ids = JSON.parse(stored);
      ids = ids.filter(id => id !== project.id);
      ids.unshift(project.id);
      ids = ids.slice(0, MAX);
      localStorage.setItem(KEY, JSON.stringify(ids));
      this.recentProjectIds = ids;
      // Filter projects to get recent ones
      const recent = ids.map(id => this.projects().find(p => p.id === id)).filter(Boolean) as Project[];
      this.recentProjects.set(recent);
    } catch { /* ignore */ }
  }

  private async loadDashboard(): Promise<void> {
    const allEpics = (
      await Promise.all(
        this.projects().map((project) => firstValueFrom(this.api.listEpics(project.id))),
      )
    ).flat();
    this.epics.set(allEpics);
    const allStories = (
      await Promise.all(allEpics.map((epic) => firstValueFrom(this.api.listStories(epic.id))))
    ).flat();
    this.stories.set(allStories);
    const allTasks = (
      await Promise.all(allStories.map((story) => firstValueFrom(this.api.listTasks(story.id))))
    ).flat();
    // Story 视图使用 loadStoryTasks 独立加载自身任务；非 story 视图才写入全局 tasks()
    if (this.view() !== 'story') this.tasks.set(allTasks);
  }

  async refresh(): Promise<void> {
    await this.loadRoute();
  }

  openAuth(mode: 'login' | 'register' = 'login'): void {
    this.showLogin(mode);
  }

  /** 进入独立登录页（全屏，非弹窗） */
  private showLogin(mode: 'login' | 'register' = 'login'): void {
    this.authMode.set(mode);
    this.authVisible.set(true);
    if (this.router.url !== '/login') {
      void this.router.navigateByUrl('/login');
    }
  }

  closeAuth(): void {
    this.authVisible.set(false);
  }

  async authenticate(username: string, password: string): Promise<void> {
    this.submitting.set(true);
    try {
      const result = await firstValueFrom(
        this.authMode() === 'register'
          ? this.api.register(username.trim(), password)
          : this.api.login(username.trim(), password),
      );
      localStorage.setItem('agentboard_token', result.token);
      localStorage.setItem('agentboard_user', result.username);
      localStorage.setItem('agentboard_is_admin', String(result.is_admin ?? false));
      this.currentUser.set(result.username);
      this.isAdmin.set(result.is_admin ?? false);
      this.authVisible.set(false);
      this.notify(this.authMode() === 'register' ? '注册成功，已登录' : '登录成功');
      if (this.router.url.startsWith('/login')) {
        await this.router.navigateByUrl('/');
      } else {
        await this.loadRoute();
      }
    } catch (error) {
      this.notify(
        `${this.authMode() === 'register' ? '注册' : '登录'}失败：${this.message(error)}`,
        'error',
      );
    } finally {
      this.submitting.set(false);
    }
  }

  logout(): void {
    localStorage.removeItem('agentboard_token');
    localStorage.removeItem('agentboard_user');
    localStorage.removeItem('agentboard_is_admin');
    this.currentUser.set('');
    this.isAdmin.set(false);
    this.isOwner.set(false);
    this.showLogin('login');
    this.notify('已退出登录');
  }

  openCreate(kind: CreateKind, parentId?: number, projectId?: number): void {
    this.modal.set({ kind, parentId, projectId });
  }

  openCreateSchedule(projectId: number): void {
    this.sprintModalOpen.set(projectId);
    this.sprintName.set('');
    this.sprintType.set('cron');
    this.sprintCron.set('');
  }
  closeSprintModal(): void {
    this.sprintModalOpen.set(null);
  }
  async submitSprintModal(): Promise<void> {
    const pid = this.sprintModalOpen();
    const title = this.sprintName().trim();
    if (!pid || !title) { this.notify('请填写计划名称', 'error'); return; }
    const type = this.sprintType();
    const cron = type === 'cron' ? this.sprintCron().trim() : '';
    if (type === 'cron' && !cron) {
      this.notify('Cron 类型需要 cron 表达式', 'error');
      return;
    }
    this.sprintModalOpen.set(null);
    await this.createNewSchedule(pid, title, type, cron);
  }

  closeCreate(): void {
    this.modal.set(null);
  }

  modalTitle(): string {
    return (
      { project: '新建项目', epic: '新建 Epic', story: '新建 Story', task: '新建工作项' } as const
    )[this.modal()?.kind || 'project'];
  }

  async create(event: Event): Promise<void> {
    event.preventDefault();
    const modal = this.modal();
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    const title = String(data.get('title') || '');
    const key = String(data.get('key') || '');
    const description = String(data.get('description') || '');
    const type = String(data.get('type') || 'task') as ItemType;
    const priority = String(data.get('priority') || 'medium') as Priority;
    const dueDate = String(data.get('due_date') || '') || null;
    const labelsStr = String(data.get('labels') || '').trim();
    const assigneeRaw = data.get('assignee_id');
    const assigneeId = assigneeRaw ? Number(assigneeRaw) : null;
    const labels = labelsStr ? JSON.stringify(labelsStr.split(',').map(s => s.trim()).filter(Boolean)) : '[]';
    if (!modal || !title.trim()) return;
    this.submitting.set(true);
    try {
      if (modal.kind === 'project') {
        await firstValueFrom(
          this.api.createProject({ name: title.trim(), key: key.trim() || undefined, description }),
        );
      } else if (modal.kind === 'epic' && modal.parentId) {
        await firstValueFrom(
          this.api.createEpic(modal.parentId, { title: title.trim(), description }),
        );
      } else if (modal.kind === 'story' && modal.parentId) {
        await firstValueFrom(
          this.api.createStory(modal.parentId, { title: title.trim(), description }),
        );
      } else if (modal.kind === 'task' && modal.parentId && modal.projectId) {
        await firstValueFrom(
          this.api.createTask(modal.parentId, {
            project_id: modal.projectId,
            title: title.trim(),
            description,
            type,
            priority,
            due_date: dueDate,
            labels,
            assignee_id: assigneeId,
          }),
        );
      }
      this.modal.set(null);
      this.notify('创建成功');
      await this.refresh();
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async saveProject(name: string, key: string, description: string): Promise<void> {
    const project = this.project();
    if (!project) return;
    await this.run('项目已更新', () =>
      firstValueFrom(this.api.updateProject(project.id, { name, key: key || null, description })),
    );
  }

  async saveEpic(title: string, description: string, status: Status): Promise<void> {
    const epic = this.epic();
    if (!epic) return;
    await this.run('Epic 已更新', () =>
      firstValueFrom(this.api.updateEpic(epic.id, { title, description, status })),
    );
  }

  async saveStory(title: string, description: string, status: Status): Promise<void> {
    const story = this.story();
    if (!story) return;
    await this.run('Story 已更新', () =>
      firstValueFrom(this.api.updateStory(story.id, { title, description, status })),
    );
  }

  async saveTask(
    title: string,
    description: string,
    spec: string,
    type: ItemType,
    priority: Priority,
    dueDate: string | null,
  ): Promise<void> {
    const task = this.task();
    if (!task) return;
    await this.run('任务已保存', () =>
      firstValueFrom(this.api.updateTask(task.id, { title, description, spec, type, priority, due_date: dueDate })),
    );
  }

  // Task 编辑弹窗：打开并预填充当前任务字段
  openTaskEditModal(task: Task): void {
    this.taskEditModal.set(task);
    this.taskEditTitle.set(task.title);
    this.taskEditType.set(task.type);
    this.taskEditPriority.set(task.priority);
    this.taskEditDueDate.set(task.due_date || null);
    this.taskEditLabels.set(this.labelsToString(task.labels));
    this.taskEditSprintId.set(task.sprint_id || null);
    this.taskEditAssigneeId.set(task.assignee_id || null);
    this.taskEditDescription.set(task.description || '');
    this.taskEditSpec.set(task.spec || '');
  }

  closeTaskEditModal(): void {
    this.taskEditModal.set(null);
  }

  async submitTaskEditModal(): Promise<void> {
    const task = this.taskEditModal();
    if (!task) return;
    const title = this.taskEditTitle().trim();
    if (!title) {
      this.notify('标题不能为空', 'error');
      return;
    }
    const labels = this.taskEditLabels().trim()
      ? JSON.stringify(this.taskEditLabels().split(',').map(s => s.trim()).filter(Boolean))
      : '[]';
    this.submitting.set(true);
    try {
      await firstValueFrom(
        this.api.updateTask(task.id, {
          title,
          type: this.taskEditType(),
          priority: this.taskEditPriority(),
          due_date: this.taskEditDueDate() || null,
          labels,
          sprint_id: this.taskEditSprintId(),
          assignee_id: this.taskEditAssigneeId(),
          description: this.taskEditDescription(),
          spec: this.taskEditSpec(),
        }),
      );
      this.taskEditModal.set(null);
      this.notify('任务已更新');
      await this.refresh();
    } catch (error) {
      this.notify(`更新失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  // B-01: Save task labels
  async saveTaskLabels(labelsInput: string): Promise<void> {
    const task = this.task();
    if (!task) return;
    const labels = labelsInput.trim()
      ? JSON.stringify(labelsInput.split(',').map(s => s.trim()).filter(Boolean))
      : '[]';
    await this.run('标签已保存', () =>
      firstValueFrom(this.api.updateTask(task.id, { labels })),
    );
  }

  // B-02: Save task assignee (负责人)
  async saveTaskAssignee(taskId: number, userId: number): Promise<void> {
    await this.run('负责人已更新', () =>
      firstValueFrom(this.api.updateTask(taskId, { assignee_id: userId > 0 ? userId : null })),
    );
  }

  async changeTaskStatus(status: Status): Promise<void> {
    const task = this.task();
    if (!task || task.status === status) return;
    await this.run('状态已更新', () => firstValueFrom(this.api.setTaskStatus(task.id, status)));
  }

  async addComment(event: Event, author: string, content: string): Promise<void> {
    event.preventDefault();
    const task = this.task();
    if (!task || !author.trim() || !content.trim()) return;
    localStorage.setItem('agentboard_comment_author', author.trim());
    await this.run('评论已发布', () =>
      firstValueFrom(
        this.api.addComment(task.id, { author: author.trim(), content: content.trim() }),
      ),
    );
  }

  commentAuthor(): string {
    return localStorage.getItem('agentboard_comment_author') || this.currentUser() || '我';
  }

  // Task 603: 复制文本到剪贴板
  copyToClipboard(text: string): void {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => this.notify('已复制到剪贴板'));
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      this.notify('已复制到剪贴板');
    }
  }

  private openConfirmation(
    options: Omit<ConfirmationDialog, 'action' | 'cancelLabel'> & { cancelLabel?: string },
    action: () => Promise<void>,
  ): void {
    if (this.confirmation() || this.confirmationBusy()) return;
    this.confirmation.set({
      ...options,
      cancelLabel: options.cancelLabel || '取消',
      action,
    });
    setTimeout(() => this.document.getElementById('confirmation-primary')?.focus());
  }

  cancelConfirmation(): void {
    if (this.confirmationBusy()) return;
    this.confirmation.set(null);
  }

  async acceptConfirmation(): Promise<void> {
    const dialog = this.confirmation();
    if (!dialog || this.confirmationBusy()) return;
    this.confirmationBusy.set(true);
    try {
      await dialog.action();
      this.confirmation.set(null);
    } catch (error) {
      this.notify(`操作失败：${this.message(error)}`, 'error');
    } finally {
      this.confirmationBusy.set(false);
    }
  }

  remove(kind: 'project' | 'epic' | 'story' | 'task', id: number): void {
    const labels: Record<typeof kind, string> = {
      project: '项目',
      epic: 'Epic',
      story: 'Story',
      task: '任务',
    };
    const label = labels[kind];
    this.openConfirmation({
      title: `删除${label}？`,
      message: `删除后，该${label}及其关联数据将无法恢复。请确认是否继续。`,
      confirmLabel: `删除${label}`,
      tone: 'danger',
    }, async () => {
      try {
        if (kind === 'project') await firstValueFrom(this.api.deleteProject(id));
        if (kind === 'epic') await firstValueFrom(this.api.deleteEpic(id));
        if (kind === 'story') await firstValueFrom(this.api.deleteStory(id));
        if (kind === 'task') await firstValueFrom(this.api.deleteTask(id));
        this.notify('已删除');
        await this.router.navigateByUrl(
          kind === 'project'
            ? '/projects'
            : kind === 'epic'
              ? `/project/${this.project()?.id}`
              : kind === 'story'
                ? `/epic/${this.epic()?.id}`
                : `/story/${this.story()?.id}`,
        );
      } catch (error) {
        this.notify(`删除失败：${this.message(error)}`, 'error');
      }
    });
  }

  /* ---------- Sprint ---------- */

  async createSprint(event: Event, name: string, goal: string): Promise<void> {
    event.preventDefault();
    const project = this.project();
    if (!project || !name.trim()) return;
    this.submitting.set(true);
    try {
      await firstValueFrom(this.api.createSprint(project.id, { title: name.trim(), goal }));
      this.notify('Sprint 已创建');
      await this.loadSprints(project.id);
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async loadSprints(projectId: number): Promise<void> {
    this.sprints.set(await firstValueFrom(this.api.listSprints(projectId)));
  }

  async loadBacklog(projectId: number): Promise<void> {
    try {
      const tasks = await firstValueFrom(
        this.api.searchTasks({ project_id: projectId, limit: 200 }),
      );
      this.backlogTasks.set(tasks.filter((t) => !t.sprint_id));
    } catch {
      this.backlogTasks.set([]);
    }
  }

  /** 分页加载 Story 的任务（修复：确保只加载当前 story 的 task/bug） */
  async loadStoryTasks(storyId: number, page: number): Promise<void> {
    const limit = this.storyTaskPageSize;
    const offset = (page - 1) * limit;
    try {
      const result = await firstValueFrom(this.api.listTasksPaginated(storyId, limit, offset));
      // result: { items: Task[], total: number }
      this.tasks.set(result.items || []);
      this.storyTaskTotal.set(result.total || (result.items || []).length);
      this.storyTaskPage.set(page);
      // 计算总页数
      const totalPages = Math.max(1, Math.ceil((result.total || 0) / limit));
      this.taskPageCount.set(totalPages);
    } catch {
      this.tasks.set([]);
      this.storyTaskTotal.set(0);
    }
  }

  /** Story 任务翻页 */
  async goStoryTaskPage(page: number): Promise<void> {
    const storyId = this.story()?.id;
    if (!storyId || page < 1) return;
    const maxPages = this.taskPageCount();
    if (page > maxPages) return;
    await this.loadStoryTasks(storyId, page);
  }

  readonly backlogVisibleTasks = computed(() => {
    const query = this.search().trim().toLocaleLowerCase();
    return query
      ? this.backlogTasks().filter((t) =>
          `${t.title} ${t.description} ${t.spec}`.toLocaleLowerCase().includes(query),
        )
      : this.backlogTasks();
  });

  activateSprint(id: number): void {
    this.openConfirmation({
      title: '启动 Sprint？',
      message: '启动后，此 Sprint 将进入进行中状态。同一项目同时只能有一个进行中的 Sprint。',
      confirmLabel: '确认启动',
      tone: 'info',
    }, async () => {
      try {
        await firstValueFrom(this.api.activateSprint(id));
        this.notify('Sprint 已激活');
        await this.refresh();
      } catch (error) {
        this.notify(`激活失败：${this.message(error)}`, 'error');
      }
    });
  }

  completeSprint(id: number): void {
    this.openConfirmation({
      title: '完成 Sprint？',
      message: '完成后，所有未完成的任务会自动退回 Backlog。',
      confirmLabel: '确认完成',
      tone: 'warning',
    }, async () => {
      try {
        await firstValueFrom(this.api.completeSprint(id));
        this.notify('Sprint 已完成，未完成任务已退回 Backlog');
        await this.refresh();
      } catch (error) {
        this.notify(`完成失败：${this.message(error)}`, 'error');
      }
    });
  }

  deleteSprint(id: number): void {
    this.openConfirmation({
      title: '删除 Sprint？',
      message: '删除后无法恢复，其中的任务将不再属于此 Sprint。',
      confirmLabel: '删除 Sprint',
      tone: 'danger',
    }, async () => {
      try {
        await firstValueFrom(this.api.deleteSprint(id));
        this.notify('Sprint 已删除');
        await this.refresh();
      } catch (error) {
        this.notify(`删除失败：${this.message(error)}`, 'error');
      }
    });
  }

  deleteSchedule(id: number): void {
    const project = this.project();
    this.openConfirmation({
      title: '删除定时计划？',
      message: '删除后，该计划将不再执行且无法恢复。',
      confirmLabel: '删除计划',
      tone: 'danger',
    }, async () => {
      try {
        await firstValueFrom(this.api.deleteSchedule(id));
        this.notify('计划已删除');
        if (project) await this.loadSchedules(project.id);
      } catch (error) {
        this.notify(`删除失败：${this.message(error)}`, 'error');
      }
    });
  }

  async assignTaskToSprint(taskId: number, sprintId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.updateTask(taskId, { sprint_id: sprintId } as Partial<Task>));
      this.notify('任务已加入 Sprint');
      await this.refresh();
    } catch (error) {
      this.notify(`分配失败：${this.message(error)}`, 'error');
    }
  }

  async removeTaskFromSprint(taskId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.updateTask(taskId, { sprint_id: null } as Partial<Task>));
      this.notify('任务已移出 Sprint');
      await this.refresh();
    } catch (error) {
      this.notify(`移除失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Bulk Operations ---------- */
  // Epic 21 Story 21.3: Shift+点击多选支持
  toggleTaskSelection(taskId: number, event?: Event): void {
    const selected = new Set(this.selectedTasks());
    const mouseEvent = event as MouseEvent | undefined;
    
    // Shift+点击：范围选择
    if (mouseEvent?.shiftKey && this.lastSelectedTaskId() !== null) {
      const tasks = this.visibleTasks();
      const lastIdx = tasks.findIndex(t => t.id === this.lastSelectedTaskId());
      const currentIdx = tasks.findIndex(t => t.id === taskId);
      if (lastIdx >= 0 && currentIdx >= 0) {
        const [start, end] = lastIdx < currentIdx ? [lastIdx, currentIdx] : [currentIdx, lastIdx];
        for (let i = start; i <= end; i++) {
          selected.add(tasks[i].id);
        }
      }
    } else {
      // 普通点击切换
      if (selected.has(taskId)) {
        selected.delete(taskId);
      } else {
        selected.add(taskId);
      }
    }
    
    this.selectedTasks.set(selected);
    this.lastSelectedTaskId.set(taskId);
  }

  selectAllTasks(): void {
    const allIds = new Set(this.visibleTasks().map(t => t.id));
    this.selectedTasks.set(allIds);
  }

  clearTaskSelection(): void {
    this.selectedTasks.set(new Set());
    this.lastSelectedTaskId.set(null);
  }

  get selectedTaskCount(): number {
    return this.selectedTasks().size;
  }

  // Epic 21 Story 21.3: 批量操作进度跟踪
  async bulkUpdateStatus(newStatus: string): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;
    
    // 显示进度
    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在更新 0/${ids.length} 个任务…` });
    
    try {
      const result = await firstValueFrom(this.api.bulkUpdateTasks(ids, { status: newStatus }));
      const successCount = result.updated?.length ?? 0;
      const errorCount = result.errors?.length ?? 0;
      
      // Story 21.3: 失败反馈优化 - 显示具体失败项
      if (errorCount > 0) {
        const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
        const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
        this.notify(`批量更新完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
      } else {
        this.notify(`已批量更新 ${successCount} 个任务的状态为「${this.statusLabel(newStatus)}」`);
      }
      this.clearTaskSelection();
      await this.refresh();
    } catch (error) {
      // Epic 21 Story 21.4: 离线队列重放时的错误处理
      const errorMsg = error instanceof Error ? error.message : String(error);
      if (errorMsg.includes('离线')) {
        this.notify('操作已加入离线队列，将在网络恢复后自动重试', 'error');
      } else {
        this.notify(`批量更新失败：${errorMsg}`, 'error');
      }
    } finally {
      this.bulkProgress.set(null);
    }
  }

  // v3.0: 批量指派（复用现有 bulkUpdateTasks 的 assignee_id / clear_assignee 字段，增量后端变更）
  async bulkUpdateAssignee(newAssigneeId: number | null): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;

    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在指派 0/${ids.length} 个任务…` });

    try {
      const updates = newAssigneeId === null
        ? { clear_assignee: true }
        : { assignee_id: newAssigneeId };
      const result = await firstValueFrom(this.api.bulkUpdateTasks(ids, updates));
      const successCount = result.updated?.length ?? 0;
      const errorCount = result.errors?.length ?? 0;

      if (errorCount > 0) {
        const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
        const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
        this.notify(`批量指派完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
      } else {
        const name = newAssigneeId === null ? '未指派' : this.getAssigneeName(newAssigneeId);
        this.notify(`已批量指派 ${successCount} 个任务给「${name}」`);
      }
      this.clearTaskSelection();
      await this.refresh();
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      this.notify(`批量指派失败：${errorMsg}`, 'error');
    } finally {
      this.bulkProgress.set(null);
    }
  }

  // v3.2: 批量改截止日期（复用现有 bulkUpdateTasks 的 due_date / clear_due_date 字段，增量后端变更）
  async bulkUpdateDueDate(newDueDate: string | null): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;

    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在更新 0/${ids.length} 个任务…` });

    try {
      const updates = newDueDate === null
        ? { clear_due_date: true }
        : { due_date: newDueDate };
      const result = await firstValueFrom(this.api.bulkUpdateTasks(ids, updates));
      const successCount = result.updated?.length ?? 0;
      const errorCount = result.errors?.length ?? 0;

      if (errorCount > 0) {
        const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
        const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
        this.notify(`批量更新完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
      } else {
        const label = newDueDate === null ? '已清除截止日期' : `截止日期设为 ${newDueDate}`;
        this.notify(`已批量更新 ${successCount} 个任务的${label}`);
      }
      this.clearTaskSelection();
      await this.refresh();
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      if (errorMsg.includes('离线')) {
        this.notify('操作已加入离线队列，将在网络恢复后自动重试', 'error');
      } else {
        this.notify(`批量更新失败：${errorMsg}`, 'error');
      }
    } finally {
      this.bulkProgress.set(null);
      this.bulkDueDateValue.set('');
    }
  }

  // v2.9: 批量修改优先级（复用现有 bulkUpdateTasks 的 priority 字段，零后端契约变更）
  async bulkUpdatePriority(newPriority: string): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;

    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在更新 0/${ids.length} 个任务…` });

    try {
      const result = await firstValueFrom(this.api.bulkUpdateTasks(ids, { priority: newPriority }));
      const successCount = result.updated?.length ?? 0;
      const errorCount = result.errors?.length ?? 0;

      if (errorCount > 0) {
        const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
        const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
        this.notify(`批量更新完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
      } else {
        this.notify(`已批量更新 ${successCount} 个任务的优先级为「${this.priorityLabel(newPriority)}」`);
      }
      this.clearTaskSelection();
      await this.refresh();
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      if (errorMsg.includes('离线')) {
        this.notify('操作已加入离线队列，将在网络恢复后自动重试', 'error');
      } else {
        this.notify(`批量更新失败：${errorMsg}`, 'error');
      }
    } finally {
      this.bulkProgress.set(null);
    }
  }

  bulkDeleteTasks(): void {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;
    this.openConfirmation({
      title: `删除 ${ids.length} 个任务？`,
      message: '所选任务会被永久删除，此操作无法撤销。',
      confirmLabel: `删除 ${ids.length} 个任务`,
      tone: 'danger',
    }, async () => {
      // 显示进度
      this.bulkProgress.set({ current: 0, total: ids.length, message: `正在删除 0/${ids.length} 个任务…` });

      try {
        const result = await firstValueFrom(this.api.bulkDeleteTasks(ids));
        const successCount = result.deleted?.length ?? 0;
        const errorCount = result.errors?.length ?? 0;

        // Story 21.3: 失败反馈优化 - 显示具体失败项
        if (errorCount > 0) {
          const failedIds = result.errors.map((e: any) => e.id || e.task_id).filter(Boolean).slice(0, 3);
          const failedMsg = failedIds.length > 0 ? `（失败 ID: ${failedIds.join(', ')}${errorCount > 3 ? '…' : ''}）` : '';
          this.notify(`批量删除完成：${successCount} 成功，${errorCount} 失败${failedMsg}`, 'error');
        } else {
          this.notify(`已删除 ${successCount} 个任务`);
        }
        this.clearTaskSelection();
        await this.refresh();
      } catch (error) {
        // Epic 21 Story 21.4: 离线队列重放时的错误处理
        const errorMsg = error instanceof Error ? error.message : String(error);
        if (errorMsg.includes('离线')) {
          this.notify('操作已加入离线队列，将在网络恢复后自动重试', 'error');
        } else {
          this.notify(`批量删除失败：${errorMsg}`, 'error');
        }
      } finally {
        this.bulkProgress.set(null);
      }
    });
  }

  // Task 711: 批量删除 - 快捷键触发
  bulkDelete(): void {
    if (this.selectedTasks().size > 0) {
      void this.bulkDeleteTasks();
    }
  }

  showBulkActionPanel(type: 'status' | 'delete' | 'priority' | 'assignee' | 'due'): void {
    this.bulkActionTarget.set(type);
  }

  closeBulkActionPanel(): void {
    this.bulkActionTarget.set(null);
  }

  /* ---------- Keyboard Navigation ---------- */
  handleTaskKeydown(event: KeyboardEvent): void {
    // Skip if in input/textarea/select
    const target = event.target as HTMLElement;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') {
      return;
    }
    const tasks = this.visibleTasks();
    if (tasks.length === 0) return;
    const curId = this.focusedTaskId();
    const idx = curId == null ? -1 : tasks.findIndex((t) => t.id === curId);
    switch (event.key) {
      case 'j':
      case 'ArrowDown':
        event.preventDefault();
        if (idx < tasks.length - 1) {
          this.focusedTaskId.set(tasks[idx + 1].id);
          this.scrollToFocusedTask();
        }
        break;
      case 'k':
      case 'ArrowUp':
        event.preventDefault();
        if (idx > 0) {
          this.focusedTaskId.set(tasks[idx - 1].id);
          this.scrollToFocusedTask();
        } else if (idx === -1 && tasks.length > 0) {
          this.focusedTaskId.set(tasks[0].id);
          this.scrollToFocusedTask();
        }
        break;
      case 'Enter':
        if (idx >= 0 && idx < tasks.length) {
          event.preventDefault();
          this.router.navigate(['/task', tasks[idx].id]);
        }
        break;
      case ' ':
        if (idx >= 0 && idx < tasks.length) {
          event.preventDefault();
          this.toggleTaskSelection(tasks[idx].id);
        }
        break;
      case '/':
        // Epic 32: 快速聚焦任务搜索框（与 GitHub/Jira 一致）
        event.preventDefault();
        const searchEl = document.querySelector<HTMLInputElement>('.task-search-input');
        if (searchEl) {
          searchEl.focus();
          searchEl.select();
        }
        break;
      case 'Escape':
        this.focusedTaskId.set(null);
        this.clearTaskSelection();
        this.closeBulkActionPanel();
        break;
      case 'a':
        // Ctrl+A / Cmd+A: Select all tasks
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault();
          this.selectAllTasks();
        }
        break;
    }
  }

  private scrollToFocusedTask(): void {
    setTimeout(() => {
      const el = document.querySelector('.entity-item.kbd-focused');
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }, 0);
  }

  isTaskFocused(taskId: number): boolean {
    return this.focusedTaskId() === taskId;
  }

  async loadSprintBurndown(sprintId: number): Promise<void> {
    try {
      const data = await firstValueFrom(this.api.getSprintBurndown(sprintId));
      this.sprintBurndown.set(data);
    } catch {
      this.sprintBurndown.set(null);
    }
  }

  sprintStatusLabel(status: string): string {
    return (
      (
        { planning: '规划中', active: '进行中', completed: '已完成' } as Record<string, string>
      )[status] || status
    );
  }

  /* ---------- Members ---------- */
  async loadMembers(projectId: number): Promise<void> {
    try {
      const result = await firstValueFrom(this.api.listMembers(projectId));
      this.members.set(result.items);
    } catch {
      this.members.set([]);
    }
  }

  async checkProjectOwner(projectId: number): Promise<void> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) { this.isOwner.set(false); this.isAdmin.set(false); return; }
    try {
      const me = await firstValueFrom(this.api.me());
      this.isAdmin.set(me.is_admin ?? false);
      // 从成员列表判断是否 owner
      const result = await firstValueFrom(this.api.listMembers(projectId));
      const myMember = result.items.find((m: ProjectMember) => m.user_id === me.id);
      this.isOwner.set(myMember?.role === 'owner');
    } catch {
      this.isOwner.set(false); this.isAdmin.set(false);
    }
  }

  async addMember(projectId: number, username: string, role: string = 'member'): Promise<void> {
    try {
      await firstValueFrom(this.api.addMember(projectId, { username, role }));
      this.notify('成员已添加');
      await this.loadMembers(projectId);
    } catch (error) {
      this.notify(`添加失败：${this.message(error)}`, 'error');
    }
  }

  removeMember(projectId: number, userId: number): void {
    this.openConfirmation({
      title: '移除项目成员？',
      message: '该成员将失去项目访问权限，之后仍可重新邀请。',
      confirmLabel: '确认移除',
      tone: 'warning',
    }, async () => {
      try {
        await firstValueFrom(this.api.removeMember(projectId, userId));
        this.notify('成员已移除');
        await this.loadMembers(projectId);
      } catch (error) {
        this.notify(`移除失败：${this.message(error)}`, 'error');
      }
    });
  }

  async updateMemberRole(projectId: number, userId: number, role: string): Promise<void> {
    try {
      await firstValueFrom(this.api.updateMemberRole(projectId, userId, role));
      this.notify('角色已更新');
      await this.loadMembers(projectId);
    } catch (error) {
      this.notify(`更新失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Notifications ---------- */
  async loadNotifications(): Promise<void> {
    try {
      const [notifs, count] = await Promise.all([
        firstValueFrom(this.api.listNotifications({ limit: 20 })),
        firstValueFrom(this.api.getUnreadCount()),
      ]);
      this.notifications.set(notifs.items);
      this.unreadCount.set(count.count);
    } catch {
      this.notifications.set([]);
      this.unreadCount.set(0);
    }
  }

  async markRead(notifId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.markRead(notifId));
      await this.loadNotifications();
    } catch { /* ignore */ }
  }

  async markAllRead(): Promise<void> {
    try {
      await firstValueFrom(this.api.markAllRead());
      await this.loadNotifications();
    } catch { /* ignore */ }
  }

  async deleteNotification(notifId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.deleteNotification(notifId));
      await this.loadNotifications();
    } catch { /* ignore */ }
  }

  toggleNotifications(): void {
    const current = this.showNotifications();
    this.showNotifications.set(!current);
    if (!current) { void this.loadNotifications(); }
  }

  /* ---------- Project Stats ---------- */
  async loadProjectStats(projectId: number): Promise<void> {
    try {
      const stats = await firstValueFrom(this.api.getProjectStats(projectId));
      this.projectStats.set(stats);
    } catch {
      this.projectStats.set(null);
    }
  }

  /* ---------- Attachment ---------- */
  async loadAttachments(taskId: number): Promise<void> {
    try {
      this.attachments.set(await firstValueFrom(this.api.listAttachments(taskId)));
    } catch {
      this.attachments.set([]);
    }
  }

  async onAttachmentFileSelected(event: Event, taskId: number): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    try {
      await firstValueFrom(this.api.uploadAttachment(taskId, file));
      this.notify('附件已上传');
      await this.loadAttachments(taskId);
    } catch (error) {
      this.notify(`上传失败：${this.message(error)}`, 'error');
    }
    input.value = '';
  }

  deleteAttachment(taskId: number, attachmentId: number): void {
    this.openConfirmation({
      title: '删除附件？',
      message: '附件删除后无法恢复，请确认是否继续。',
      confirmLabel: '删除附件',
      tone: 'danger',
    }, async () => {
      try {
        await firstValueFrom(this.api.deleteAttachment(attachmentId));
        this.notify('附件已删除');
        await this.loadAttachments(taskId);
      } catch (error) {
        this.notify(`删除失败：${this.message(error)}`, 'error');
      }
    });
  }

  /* ---------- Schedules ---------- */
  async loadSchedules(projectId: number): Promise<void> {
    try {
      this.schedules.set(await firstValueFrom(this.api.listSchedules(projectId)));
    } catch {
      this.schedules.set([]);
    }
  }

  async toggleSchedule(scheduleId: number, enabled: boolean): Promise<void> {
    try {
      await firstValueFrom(this.api.updateSchedule(scheduleId, { enabled } as Partial<AgentSchedule>));
      this.notify(enabled ? '计划已启用' : '计划已停用');
      const project = this.project();
      if (project) await this.loadSchedules(project.id);
    } catch (error) {
      this.notify(`操作失败：${this.message(error)}`, 'error');
    }
  }

  async createNewSchedule(projectId: number, title: string, scheduleType: string, cronExpr: string): Promise<void> {
    try {
      await firstValueFrom(this.api.createSchedule(projectId, {
        title,
        schedule_type: scheduleType as 'cron' | 'once',
        cron_expr: cronExpr || undefined,
      }));
      this.notify('计划已创建');
      await this.loadSchedules(projectId);
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    }
  }

  async saveProjectSettings(name: string, key: string, description: string): Promise<void> {
    const project = this.project();
    if (!project) return;
    try {
      await firstValueFrom(this.api.updateProject(project.id, { name, key: key || null, description }));
      this.notify('项目设置已保存');
      await this.loadRoute();
    } catch (error) {
      this.notify(`保存失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Admin ---------- */
  async adminMe(): Promise<{ id: number; username: string; is_admin: boolean } | null> {
    const token = localStorage.getItem('agentboard_token');
    if (!token) return null;
    try {
      return await firstValueFrom(this.api.me());
    } catch {
      return null;
    }
  }

  async loadAdminData(): Promise<void> {
    try {
      const [usersResult, projectsResult] = await Promise.all([
        firstValueFrom(this.api.adminListUsers({ limit: 100 })),
        firstValueFrom(this.api.adminListProjects({ limit: 100 })),
      ]);
      this.adminUsers.set(usersResult.items);
      this.adminProjects.set(projectsResult.items);
    } catch {
      this.adminUsers.set([]);
      this.adminProjects.set([]);
    }
  }

  async setAdmin(userId: number, isAdmin: boolean): Promise<void> {
    try {
      await firstValueFrom(this.api.adminUpdateUser(userId, isAdmin));
      this.notify(isAdmin ? '已设为管理员' : '已撤销管理员权限');
      await this.loadAdminData();
    } catch (error) {
      this.notify(`操作失败：${this.message(error)}`, 'error');
    }
  }

  adminDeleteProject(projectId: number): void {
    this.openConfirmation({
      title: '永久删除项目？',
      message: '该项目及其所有关联数据都会被永久删除，此操作无法撤销。',
      confirmLabel: '永久删除',
      tone: 'danger',
    }, async () => {
      try {
        await firstValueFrom(this.api.adminDeleteProject(projectId));
        this.notify('项目已删除');
        await this.loadAdminData();
      } catch (error) {
        this.notify(`删除失败：${this.message(error)}`, 'error');
      }
    });
  }

  /* ---------- Epic 25: API Keys ---------- */
  async loadApiKeys(): Promise<void> {
    try {
      const resp = await firstValueFrom(this.api.listApiKeys());
      this.apiKeys.set(resp.items || []);
    } catch {
      this.apiKeys.set([]);
    }
  }

  async loadProfile(): Promise<void> {
    try {
      this.profile.set(await firstValueFrom(this.api.me()));
    } catch {
      this.profile.set(null);
    }
  }

  async saveProfile(displayName: string, email: string, avatarUrl: string): Promise<void> {
    this.submitting.set(true);
    try {
      const profile = await firstValueFrom(this.api.updateProfile({
        display_name: displayName.trim(), email: email.trim(), avatar_url: avatarUrl.trim(),
      }));
      this.profile.set(profile);
      this.notify('个人资料已保存');
    } catch (error) {
      this.notify(`保存失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async updatePassword(currentPassword: string, newPassword: string, confirmPassword: string): Promise<void> {
    if (newPassword !== confirmPassword) {
      this.notify('两次输入的新密码不一致', 'error');
      return;
    }
    this.submitting.set(true);
    try {
      await firstValueFrom(this.api.changePassword({ current_password: currentPassword, new_password: newPassword }));
      this.notify('密码已更新');
    } catch (error) {
      this.notify(`修改密码失败：${this.message(error)}`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async loadMyProjects(): Promise<void> {
    try {
      const response = await firstValueFrom(this.api.listMyProjects());
      this.myProjects.set(response.items || []);
    } catch {
      this.myProjects.set([]);
    }
  }

  showCreateKeyModal(): void {
    this.newKeyName.set('');
    this.newKeyPerms.set('');
    this.createdKeyPlaintext.set('');
    this.keyModalVisible.set(true);
  }

  closeKeyModal(): void {
    this.keyModalVisible.set(false);
    this.createdKeyPlaintext.set('');
  }

  async createApiKey(): Promise<void> {
    const name = this.newKeyName().trim();
    if (!name) { this.notify('请输入密钥名称', 'error'); return; }
    const perms = this.newKeyPerms().trim()
      ? this.newKeyPerms().trim().split(',').map(p => p.trim()).filter(Boolean)
      : ['api:read', 'api:write'];
    try {
      const result = await firstValueFrom(this.api.createApiKey({ name, permissions: perms }));
      this.createdKeyPlaintext.set(result.key);
      this.notify('API Key 已创建，请立即复制保存！');
      await this.loadApiKeys();
    } catch (error) {
      this.notify(`创建失败：${this.message(error)}`, 'error');
    }
  }

  revokeApiKey(keyId: number): void {
    this.openConfirmation({
      title: '撤销 API Key？',
      message: '撤销后，所有使用该 Key 的请求会立即失效，且无法恢复。',
      confirmLabel: '确认撤销',
      tone: 'danger',
    }, async () => {
      try {
        await firstValueFrom(this.api.revokeApiKey(keyId));
        this.notify('API Key 已撤销');
        await this.loadApiKeys();
      } catch (error) {
        this.notify(`撤销失败：${this.message(error)}`, 'error');
      }
    });
  }

  async openNotification(notification: Notification): Promise<void> {
    if (!notification.is_read) await this.markRead(notification.id);
    this.showNotifications.set(false);
    if (notification.link) await this.router.navigateByUrl(notification.link);
  }

  async generate(): Promise<void> {
    const task = this.task();
    if (!task) return;
    await this.run('子任务生成完成', () => firstValueFrom(this.api.generateSubtasks(task.id)));
  }

  toggleTheme(): void {
    this.applyTheme(this.document.documentElement.dataset['theme'] === 'dark' ? 'light' : 'dark');
    // Task 717: Theme change toast feedback
    this.notify(
      this.isDarkTheme() ? '已切换到深色模式 🌙' : '已切换到浅色模式 ☀️'
    );
  }

  getThemeLabel(): string {
    const isDark = this.isDarkTheme();
    const isSystem = !localStorage.getItem('agentboard_theme');
    const base = isDark ? '切换到浅色模式' : '切换到深色模式';
    return isSystem ? `${base}（跟随系统）` : base;
  }

  isDarkTheme(): boolean {
    return this.document.documentElement.dataset['theme'] === 'dark';
  }

  setBoardMode(board: boolean): void {
    this.boardMode.set(board);
    localStorage.setItem('agentboard_story_view', board ? 'board' : 'list');
  }

  toggleListDensity(): void {
    const next = this.listDensity() === 'compact' ? 'comfortable' : 'compact';
    this.listDensity.set(next);
    localStorage.setItem('agentboard_list_density', next);
  }

  tasksForStatus(status: Status): Task[] {
    // Task 714/715: 虚拟滚动 + 增量渲染优化
    // memoize: Angular signals auto-caches based on dependencies
    const all = this.visibleTasks().filter((task) => task.status === status);
    return all.slice(0, this.taskPageSize() * this.taskPageCount());
  }

  private applyTheme(theme: string): void {
    this.document.documentElement.dataset['theme'] = theme;
    localStorage.setItem('agentboard_theme', theme);
  }

  // Task 721/722: 看板列折叠/展开
  toggleColumnCollapse(status: string): void {
    const collapsed = new Set(this.collapsedColumns());
    if (collapsed.has(status)) {
      collapsed.delete(status);
    } else {
      collapsed.add(status);
    }
    this.collapsedColumns.set(collapsed);
    localStorage.setItem('agentboard_collapsed_cols', JSON.stringify([...collapsed]));
  }

  isColumnCollapsed(status: string): boolean {
    return this.collapsedColumns().has(status);
  }

  // B-04: 看板拖拽改状态
  onKanbanDragStart(event: DragEvent, task: Task): void {
    this.dragTaskId.set(task.id);
    event.dataTransfer!.effectAllowed = 'move';
    event.dataTransfer!.setData('text/plain', String(task.id));
  }

  onKanbanDragOver(event: DragEvent, status: Status): void {
    if (!this.dragTaskId()) return;
    event.preventDefault();
    event.dataTransfer!.dropEffect = 'move';
    this.dragOverStatus.set(status);
  }

  onKanbanDragLeave(_event: DragEvent, status: Status): void {
    if (this.dragOverStatus() === status) this.dragOverStatus.set(null);
  }

  async onKanbanDrop(event: DragEvent, status: Status): Promise<void> {
    event.preventDefault();
    const taskId = this.dragTaskId();
    this.dragTaskId.set(null);
    this.dragOverStatus.set(null);
    if (!taskId) return;
    const task = this.tasks().find(t => t.id === taskId);
    if (!task || task.status === status) return;
    try {
      await firstValueFrom(this.api.setTaskStatus(taskId, status));
      this.tasks.update(list => list.map(t => t.id === taskId ? { ...t, status } : t));
      this.notify('状态已更新', 'success');
    } catch { this.notify('状态更新失败', 'error'); }
  }

  onKanbanDragEnd(): void {
    this.dragTaskId.set(null);
    this.dragOverStatus.set(null);
  }

  // Task 729: 看板卡片显示 Epic 名称
  taskEpicName(task: Task): string {
    if (!task.story_id) return '';
    const story = this.stories().find(s => s.id === task.story_id);
    if (!story) return '';
    const epic = this.epics().find(e => e.id === story.epic_id);
    return epic?.title || '';
  }

  // Task 719: 通知类型分组标签
  notifTypeLabel(type: string): string {
    const labels: Record<string, string> = {
      project_invite: '📬 项目邀请',
      join_request: '📩 加入申请',
      task_assigned: '📋 任务分配',
      status_changed: '🔄 状态变更',
      mentioned: '💬 提及',
      other: '🔔 其他',
    };
    return labels[type] || labels['other'];
  }

  // Story 15.1: 单条通知项类型图标（emoji + 主题色）
  notifTypeIcon(type: string): { emoji: string; color: string } {
    const icons: Record<string, { emoji: string; color: string }> = {
      project_invite: { emoji: '📬', color: '#7c3aed' },  // violet
      join_request:   { emoji: '📩', color: '#0891b2' },  // info cyan
      task_assigned:  { emoji: '📋', color: '#4f46e5' },  // brand indigo
      status_changed: { emoji: '🔄', color: '#d97706' },  // warning orange
      mentioned:      { emoji: '💬', color: '#dc2626' },  // danger red
      other:          { emoji: '🔔', color: '#64748b' },  // slate
    };
    return icons[type] || icons['other'];
  }

  // Task 719: 对象键值对列表（用于模板中遍历 groupedNotifications）
  objectEntries(obj: Record<string, any>): [string, any][] {
    return Object.entries(obj);
  }

  // Task 741: 任务详情页显示 Epic/Story 面包屑 - 获取 Epic 名称
  getEpicName(storyId: number | null): string {
    if (!storyId) return '';
    const story = this.stories().find(s => s.id === storyId);
    if (!story) return '';
    const epic = this.epics().find(e => e.id === story.epic_id);
    return epic?.title || '';
  }

  // Task 741: 获取 Story 名称
  getStoryName(storyId: number | null): string {
    if (!storyId) return '';
    const story = this.stories().find(s => s.id === storyId);
    return story?.title || '';
  }

  // Task 803: 计算子任务数量
  getSubtaskCount(parentTaskId: number): { total: number; done: number } {
    const subtasks = this.tasks().filter(t => t.source_spec_id === parentTaskId);
    return {
      total: subtasks.length,
      done: subtasks.filter(t => t.status === 'done').length
    };
  }

  // Task 744: 获取相关任务（基于 task_dependencies）
  getRelatedTasks(): { blocks: {id: number; title: string; status: string}[]; blockedBy: {id: number; title: string; status: string}[] } {
    const deps = this.taskDependencies();
    if (!deps) return { blocks: [], blockedBy: [] };
    return {
      blocks: (deps.blockers || []).map(d => ({ id: d.task_id, title: d.task?.title || '', status: d.task?.status || '' })),
      blockedBy: (deps.blocked_by || []).map(d => ({ id: d.task_id, title: d.task?.title || '', status: d.task?.status || '' }))
    };
  }

  // Task 745: 看板列任务计数
  getStatusTaskCount(status: string): number {
    return this.tasks().filter(t => t.status === status).length;
  }

  // Task 808: 评论 Markdown 预览切换
  readonly commentPreviewMode = signal(false);
  toggleCommentPreview(): void {
    this.commentPreviewMode.set(!this.commentPreviewMode());
  }
  isCommentPreviewMode(): boolean {
    return this.commentPreviewMode();
  }

  // Task 809: 项目成员头像
  getMemberAvatar(member: any): string {
    return (member.username || '?').slice(0, 2).toUpperCase();
  }

  // Task 810: 任务指派人头像
  getAssigneeInitials(assigneeId: number | null): string {
    if (!assigneeId) return '';
    // 需要从 members 中查找
    const member = this.members().find(m => m.user_id === assigneeId);
    return member?.username?.slice(0, 2).toUpperCase() || '';
  }

  // Task 810: 获取指派人/负责人用户名
  getAssigneeName(assigneeId: number | null): string {
    if (!assigneeId) return '未指派';
    const member = this.members().find(m => m.user_id === assigneeId);
    return member?.username || `用户#${assigneeId}`;
  }

  // Task 811: 子任务完成比例
  getSubtaskProgress(parentTaskId: number): number {
    const { total, done } = this.getSubtaskCount(parentTaskId);
    if (total === 0) return 0;
    return Math.round((done / total) * 100);
  }

  // Task 811: 检查 Epic 列表是否为空
  isEpicsEmpty(): boolean {
    return this.epics().length === 0;
  }

  // Task 742: 格式化日期时间
  formatDateTime(dateStr: string | null | undefined): string {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  }

  statusLabel(status: string): string {
    return (
      (
        {
          backlog: '待规划',
          todo: '待办',
          in_progress: '进行中',
          in_review: '评审中',
          verifying: '验证中',
          done: '完成',
        } as Record<string, string>
      )[status] || status
    );
  }

  priorityLabel(priority: string): string {
    return (
      (
        { highest: '最高', high: '高', medium: '中', low: '低', lowest: '最低' } as Record<
          string,
          string
        >
      )[priority] || priority
    );
  }
  // Epic 37 (v2.5): 状态色点（复用既有 statusLabel 做文案）
  statusColor(status: string): string {
    return (
      { backlog: '#94a3b8', todo: '#64748b', in_progress: '#3b82f6', in_review: '#a855f7', verifying: '#f59e0b', done: '#22c55e' } as Record<string, string>
    )[status] || '#94a3b8';
  }

  // Task 821: 任务类型图标
  taskTypeIcon(type: string): string {
    return type === 'bug' ? '🐛' : '📋';
  }

  // Task 824: 复制任务链接
  copyTaskLink(taskId: number): void {
    const url = `${window.location.origin}/task/${taskId}`;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(() => {
        this.notify('链接已复制到剪贴板！');
      }).catch(() => {
        this.notify('复制失败，请手动复制', 'error');
      });
    } else {
      const ta = document.createElement('textarea');
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      this.notify('链接已复制到剪贴板！');
    }
  }

  timeAgo(dateStr: string): string {
    if (!dateStr) return '';
    const now = Date.now();
    const date = new Date(dateStr).getTime();
    const diff = Math.floor((now - date) / 1000);
    if (diff < 60) return `${diff}s前`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h前`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d前`;
    return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  }

  // B-01: Label helpers
  parseLabels(labelsStr: string | null | undefined): string[] {
    if (!labelsStr) return [];
    try {
      const arr = JSON.parse(labelsStr);
      return Array.isArray(arr) ? arr.map(String) : [];
    } catch {
      return [];
    }
  }

  private static readonly LABEL_PALETTE = [
    '#6366f1', '#ec4899', '#f59e0b', '#10b981', '#3b82f6',
    '#8b5cf6', '#ef4444', '#14b8a6', '#f97316', '#06b6d4',
  ];

  labelColor(label: string): string {
    let hash = 0;
    for (let i = 0; i < label.length; i++) {
      hash = ((hash << 5) - hash + label.charCodeAt(i)) | 0;
    }
    return App.LABEL_PALETTE[Math.abs(hash) % App.LABEL_PALETTE.length];
  }

  labelBg(label: string): string {
    return this.labelColor(label) + '20';
  }

  labelsToString(labelsStr: string | null | undefined): string {
    return this.parseLabels(labelsStr).join(', ');
  }

  clearLabelFilter(): void {
    this.labelFilter.set('');
  }

  // B-03: Due date helpers
  isOverdue(dueDate: string | null | undefined): boolean {
    if (!dueDate) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return new Date(dueDate) < today;
  }

  isDueSoon(dueDate: string | null | undefined): boolean {
    if (!dueDate) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const due = new Date(dueDate);
    const diffDays = Math.floor((due.getTime() - today.getTime()) / 86400000);
    return diffDays >= 0 && diffDays <= 3;
  }
  // Epic 40 (v2.8): 将任务按截止日期归入分桶：overdue(过去且未完成) / today(今天) / week(未来1~7天) / later(更晚) / none(无日期)
  private dueBucket(t: Task): 'overdue' | 'today' | 'week' | 'later' | 'none' {
    if (!t.due_date) return 'none';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const due = new Date(t.due_date);
    if (isNaN(due.getTime())) return 'none';
    const diff = Math.floor((due.getTime() - today.getTime()) / 86400000);
    if (diff < 0) return 'overdue';
    if (diff === 0) return 'today';
    if (diff >= 1 && diff <= 7) return 'week';
    return 'later';
  }

  formatDueDate(dueDate: string | null | undefined): string {
    if (!dueDate) return '';
    const d = new Date(dueDate);
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
  }

  private async run(success: string, action: () => Promise<unknown>): Promise<void> {
    try {
      await action();
      this.notify(success);
      await this.refresh();
    } catch (error) {
      this.notify(`${success.replace(/已|完成/g, '')}失败：${this.message(error)}`, 'error');
    }
  }

  private notify(message: string, type: 'success' | 'error' = 'success'): void {
    // Epic 24 Story 24.2: Toast 多行+可关闭+最大数量限制
    const id = ++this._toastCounter;
    const MAX_TOASTS = 3;
    const current = this.toasts();
    const updated: { id: number; message: string; type: 'success' | 'error' }[] = [{ id, message, type }, ...current].slice(0, MAX_TOASTS);
    this.toasts.set(updated);
    setTimeout(() => this.closeToast(id), 5000);
    // 保持旧信号兼容
    this.toastMessage.set(message);
    this.toastType.set(type);
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.toastMessage.set(''); }, 4000);
  }

  closeToast(id: number): void {
    // Epic 24 Story 24.2: 关闭指定 toast
    this.toasts.set(this.toasts().filter((t: { id: number }) => t.id !== id));
  }

  private message(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  /* ---------- Epic 22: Task Dependencies ---------- */
  loadTaskDependencies(taskId: number): void {
    void this.loadTaskDependenciesAsync(taskId);
  }

  private async loadTaskDependenciesAsync(taskId: number): Promise<void> {
    try {
      const deps = await firstValueFrom(this.api.getTaskDependencies(taskId));
      this.taskDependencies.set(deps);
    } catch (error) {
      this.notify(`加载依赖失败：${this.message(error)}`, 'error');
    }
  }

  addDependency(taskId: number, dependsOnId: number, type: string = 'blocks'): void {
    void this.addDependencyAsync(taskId, dependsOnId, type);
  }

  private async addDependencyAsync(taskId: number, dependsOnId: number, type: string): Promise<void> {
    try {
      await firstValueFrom(this.api.addTaskDependency(taskId, dependsOnId, type));
      await this.loadTaskDependenciesAsync(taskId);
      this.notify('依赖添加成功');
    } catch (error) {
      this.notify(`添加依赖失败：${this.message(error)}`, 'error');
    }
  }

  removeDependency(dependencyId: number, taskId: number): void {
    void this.removeDependencyAsync(dependencyId, taskId);
  }

  private async removeDependencyAsync(dependencyId: number, taskId: number): Promise<void> {
    try {
      await firstValueFrom(this.api.removeTaskDependency(dependencyId));
      await this.loadTaskDependenciesAsync(taskId);
      this.notify('依赖已移除');
    } catch (error) {
      this.notify(`移除依赖失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Epic 22: Webhooks ---------- */
  loadWebhooks(projectId?: number): void {
    void this.loadWebhooksAsync(projectId);
  }

  private async loadWebhooksAsync(projectId?: number): Promise<void> {
    try {
      const resp = await firstValueFrom(this.api.listWebhooks(projectId));
      this.webhooks.set(resp.items || []);
    } catch (error) {
      this.notify(`加载 Webhooks 失败：${this.message(error)}`, 'error');
    }
  }

  createWebhook(name: string, url: string, secret: string, events: string[], projectId?: number): void {
    void this.createWebhookAsync(name, url, secret, events, projectId);
  }

  private async createWebhookAsync(name: string, url: string, secret: string, events: string[], projectId?: number): Promise<void> {
    try {
      await firstValueFrom(this.api.createWebhook(projectId, { name, url, secret: secret || undefined, events }));
      await this.loadWebhooksAsync(projectId);
      this.notify('Webhook 创建成功');
    } catch (error) {
      this.notify(`创建 Webhook 失败：${this.message(error)}`, 'error');
    }
  }

  deleteWebhook(webhookId: number, projectId?: number): void {
    void this.deleteWebhookAsync(webhookId, projectId);
  }

  private async deleteWebhookAsync(webhookId: number, projectId?: number): Promise<void> {
    try {
      await firstValueFrom(this.api.deleteWebhook(webhookId));
      await this.loadWebhooksAsync(projectId);
      this.notify('Webhook 已删除');
    } catch (error) {
      this.notify(`删除 Webhook 失败：${this.message(error)}`, 'error');
    }
  }

  toggleWebhook(webhookId: number, enabled: boolean, projectId?: number): void {
    void this.toggleWebhookAsync(webhookId, enabled, projectId);
  }

  private async toggleWebhookAsync(webhookId: number, enabled: boolean, projectId?: number): Promise<void> {
    try {
      await firstValueFrom(this.api.toggleWebhook(webhookId, enabled));
      await this.loadWebhooksAsync(projectId);
    } catch (error) {
      this.notify(`更新 Webhook 失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Epic 22: Audit Logs ---------- */
  loadAuditLogs(params?: { entity_type?: string; entity_id?: number }): void {
    void this.loadAuditLogsAsync(params);
  }

  private async loadAuditLogsAsync(params?: { entity_type?: string; entity_id?: number }): Promise<void> {
    try {
      const resp = await firstValueFrom(this.api.listAuditLogs({ ...params, limit: 50 }));
      this.auditLogs.set(resp.items || []);
    } catch (error) {
      this.notify(`加载审计日志失败：${this.message(error)}`, 'error');
    }
  }

  /* ---------- Export ---------- */
  exportToCSV(tasks?: Task[]): void {
    const items = tasks || this.visibleTasks();
    if (items.length === 0) {
      this.notify('没有可导出的任务', 'error');
      return;
    }
    const headers = ['ID', '标题', '类型', '状态', '优先级', '描述', 'Spec', '创建时间', '更新时间'];
    const rows = items.map(t => [
      t.id,
      `"${(t.title || '').replace(/"/g, '""')}"`,
      t.type,
      t.status,
      t.priority,
      `"${(t.description || '').replace(/"/g, '""')}"`,
      `"${(t.spec || '').replace(/"/g, '""')}"`,
      t.created_at,
      t.updated_at,
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    this.downloadFile(csv, `tasks-${Date.now()}.csv`, 'text/csv;charset=utf-8');
    this.notify(`已导出 ${items.length} 个任务到 CSV`);
  }

  /* ---------- Epic 22: Import/Export Handlers ---------- */
  onImportFileSelected(event: Event, projectId: number): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target?.result as string);
        void this.importTasksAsync(projectId, data);
      } catch {
        this.notify('JSON 解析失败，请检查文件格式', 'error');
      }
    };
    reader.readAsText(file);
  }

  private async importTasksAsync(projectId: number, data: any): Promise<void> {
    try {
      const result = await firstValueFrom(this.api.importTasks(projectId, data.tasks || []));
      const resultEl = document.getElementById('import-result');
      if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.textContent = `导入完成：成功 ${result.imported.length} 个，失败 ${result.errors.length} 个`;
        resultEl.className = result.errors.length > 0 ? 'info-box warning' : 'info-box';
      }
      this.notify(`导入完成：成功 ${result.imported.length} 个`);
    } catch (error) {
      this.notify(`导入失败：${this.message(error)}`, 'error');
    }
  }

  onCreateWebhook(projectId: number): void {
    const nameEl = document.getElementById('wh-name') as HTMLInputElement;
    const urlEl = document.getElementById('wh-url') as HTMLInputElement;
    const secretEl = document.getElementById('wh-secret') as HTMLInputElement;
    const name = nameEl?.value?.trim();
    const url = urlEl?.value?.trim();
    const secret = secretEl?.value?.trim();
    if (!name || !url) {
      this.notify('名称和 URL 不能为空', 'error');
      return;
    }
    this.createWebhook(name, url, secret, [], projectId);
    if (nameEl) nameEl.value = '';
    if (urlEl) urlEl.value = '';
    if (secretEl) secretEl.value = '';
  }

  exportToJSON(tasks?: Task[]): void {
    const items = tasks || this.visibleTasks();
    if (items.length === 0) {
      this.notify('没有可导出的任务', 'error');
      return;
    }
    const json = JSON.stringify(items, null, 2);
    this.downloadFile(json, `tasks-${Date.now()}.json`, 'application/json');
    this.notify(`已导出 ${items.length} 个任务到 JSON`);
  }

  exportProjectTasks(): void {
    const project = this.project();
    if (!project) {
      this.notify('请先选择一个项目', 'error');
      return;
    }
    void this.exportProjectTasksAsync(project.id);
  }

  private async exportProjectTasksAsync(projectId: number): Promise<void> {
    try {
      // Fetch all epics, stories, and tasks for the project
      const epics = await firstValueFrom(this.api.listEpics(projectId));
      const allTasks: Task[] = [];
      for (const epic of epics) {
        const stories = await firstValueFrom(this.api.listStories(epic.id));
        for (const story of stories) {
          const tasks = await firstValueFrom(this.api.listTasks(story.id));
          allTasks.push(...tasks);
        }
      }
      // Export all tasks
      this.exportToCSV(allTasks);
    } catch (error) {
      this.notify(`导出失败：${this.message(error)}`, 'error');
    }
  }

  private downloadFile(content: string, filename: string, mimeType: string): void {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  // Task 714: 虚拟滚动优化 - 分页加载更多
  loadMoreTasks(): void {
    this.taskPageCount.update((n) => n + 1);
  }

  // Task 716: 全局快捷键面板 - 切换显示
  toggleShortcuts(): void {
    this.showShortcuts.set(!this.showShortcuts());
  }

  // Task 716: 全局快捷键面板 - 快捷键说明
  // Task 710/711: 增强快捷键提示面板 + 批量选择键盘支持
  readonly shortcuts = [
    { group: '导航', items: [
      { keys: ['j'], desc: '下一项' },
      { keys: ['k'], desc: '上一项' },
      { keys: ['Enter'], desc: '打开选中项' },
      { keys: ['Esc'], desc: '关闭弹层' },
    ]},
    { group: '编辑', items: [
      { keys: ['e'], desc: '编辑选中项' },
      { keys: ['n'], desc: '新建项目' },
      { keys: ['Ctrl', '↵'], desc: '提交表单' },
    ]},
    { group: '批量操作', items: [
      { keys: ['Shift', '点击'], desc: '范围多选' },
      { keys: ['Ctrl', 'A'], desc: '全选当前列表' },
      { keys: ['Ctrl', '点击'], desc: '单项切换选择' },
      { keys: ['Del'], desc: '删除选中项' },
    ]},
    { group: '视图', items: [
      { keys: ['v'], desc: '切换列表/看板' },
      { keys: ['s'], desc: '搜索' },
      { keys: ['/'], desc: '聚焦搜索框' },
    ]},
    { group: '系统', items: [
      { keys: ['?'], desc: '显示快捷键面板' },
      { keys: ['t'], desc: '切换主题' },
      { keys: ['b'], desc: '切换侧栏' },
    ]},
  ];

  /* ---------- Task 600-605: Epic 25 前端体验优化 ---------- */

  // Task 600: 看板卡片优先级色边框
  priorityBorderClass(priority: Priority): string {
    return `kanban-card--pri-${priority}`;
  }

  // Task 601: 看板卡片完成进度
  taskProgressPct(status: Status): number {
    const map: Record<string, number> = {
      backlog: 0, todo: 20, in_progress: 50, in_review: 75, verifying: 90, done: 100,
    };
    return map[status] ?? 0;
  }

  // Task 602: 高级筛选面板 - 切换/清除
  toggleFilterPriority(p: string): void {
    const cur = this.filterPriorities();
    this.filterPriorities.set(cur.includes(p) ? cur.filter(x => x !== p) : [...cur, p]);
    this.persistQuickPriority();
  }
  // Task 716: 优先级快速筛选 chips —— 单选切换（空串=全部）；再次点击同优先级则取消
  setQuickPriority(p: string): void {
    const next = !p || this.filterPriorities().includes(p) ? [] : [p];
    this.filterPriorities.set(next);
    this.persistQuickPriority();
  }
  private persistQuickPriority(): void {
    try { localStorage.setItem('agentboard_quick_priority', JSON.stringify(this.filterPriorities())); } catch { /* ignore */ }
  }
  // Epic 37 (v2.5): 状态快速筛选 chips —— 单选切换（空串=全部）；再次点击同状态则取消
  setQuickStatus(s: string): void {
    const next = !s || this.filterStatus() === s ? '' : s;
    this.filterStatus.set(next);
    this.persistQuickStatus();
  }
  private persistQuickStatus(): void {
    try { localStorage.setItem('agentboard_quick_status', this.filterStatus()); } catch { /* ignore */ }
  }
  // Epic 38 (v2.4): 任务类型快速筛选 chips —— 单选切换（空串=全部）；再次点击同类型则取消
  setQuickType(t: string): void {
    const next = !t || this.filterTypes().includes(t) ? [] : [t];
    this.filterTypes.set(next);
    this.persistQuickType();
  }
  private persistQuickType(): void {
    try { localStorage.setItem('agentboard_quick_type', JSON.stringify(this.filterTypes())); } catch { /* ignore */ }
  }
  // Epic 39 (v2.7): 指派人快速筛选 chips —— 单选切换（空串=全部）；再次点击同指派人则取消
  setQuickAssignee(id: string): void {
    const next = !id || this.filterAssignees().includes(id) ? [] : [id];
    this.filterAssignees.set(next);
    this.persistQuickAssignee();
  }
  private persistQuickAssignee(): void {
    try { localStorage.setItem('agentboard_quick_assignee', JSON.stringify(this.filterAssignees())); } catch { /* ignore */ }
  }
  // Epic 40 (v2.8): 截止日期快速筛选 chips —— 单选切换（空串=全部）；再次点击同分桶则取消
  setQuickDue(d: string): void {
    const next = !d || this.filterDueDate() === d ? '' : d;
    this.filterDueDate.set(next);
    try { localStorage.setItem('agentboard_quick_due', next); } catch { /* ignore */ }
  }
  toggleFilterType(t: string): void {
    const cur = this.filterTypes();
    this.filterTypes.set(cur.includes(t) ? cur.filter(x => x !== t) : [...cur, t]);
  }
  clearFilters(): void {
    this.filterPriorities.set([]);
    this.filterTypes.set([]);
    this.filterAssignees.set([]);
    this.filterStatus.set('');
    this.filterDueDate.set('');
    this.labelFilter.set('');
    this.filterMineOnly.set(false);
    try { localStorage.removeItem('agentboard_filter_mine'); } catch { /* ignore */ }
    try { localStorage.removeItem('agentboard_quick_due'); } catch { /* ignore */ }
    this.persistQuickPriority();
    this.persistQuickStatus();
    this.persistQuickType();
    this.persistQuickAssignee();
  }
  // Epic 34 (v2.3): 工具栏「清除全部筛选」—— 重置搜索 + 优先级 chips + 只看我 + 高级面板全部筛选条件
  clearAllFilters(): void {
    this.taskSearchQuery.set('');
    this.clearFilters();
  }
  // Epic 33 (v2.2): 当前登录用户在成员列表中的 user_id
  myUserId(): number | null {
    const me = this.currentUser();
    if (!me) return null;
    const m = this.members().find((x) => x.username === me);
    return m ? m.user_id : null;
  }
  // Epic 33 (v2.2): 切换「只看我」并持久化
  toggleFilterMine(): void {
    const next = !this.filterMineOnly();
    this.filterMineOnly.set(next);
    try { localStorage.setItem('agentboard_filter_mine', next ? '1' : '0'); } catch { /* ignore */ }
  }
  // v3.1: 筛选预设 —— 保存当前筛选组合（5 维度 chips + 搜索 + 只看我）
  togglePresetOpen(): void { this.presetOpen.update((v) => !v); }
  saveFilterPreset(): void {
    const name = this.presetName().trim();
    if (!name) return;
    const preset: FilterPreset = {
      name,
      status: this.filterStatus(),
      priority: this.filterPriorities()[0] ?? '',
      type: this.filterTypes()[0] ?? '',
      assignee: this.filterAssignees()[0] ?? '',
      due: this.filterDueDate(),
      search: this.taskSearchQuery().trim(),
      mineOnly: this.filterMineOnly(),
    };
    this.filterPresets.set([...this.filterPresets(), preset]);
    this.persistFilterPresets();
    this.presetName.set('');
  }
  applyFilterPreset(idx: number): void {
    const p = this.filterPresets()[idx];
    if (!p) return;
    this.clearAllFilters();
    if (p.status) this.setQuickStatus(p.status);
    if (p.priority) this.setQuickPriority(p.priority);
    if (p.type) this.setQuickType(p.type);
    if (p.assignee) this.setQuickAssignee(p.assignee);
    if (p.due) this.setQuickDue(p.due);
    if (p.search) this.taskSearchQuery.set(p.search);
    if (p.mineOnly) this.filterMineOnly.set(true);
    this.presetOpen.set(false);
  }
  deleteFilterPreset(idx: number): void {
    this.filterPresets.set(this.filterPresets().filter((_, i) => i !== idx));
    this.persistFilterPresets();
  }

  // Task 603: 抽屉内快速操作
  quickAdvanceStatus(): void {
    const task = this.task();
    if (!task) return;
    const order: Status[] = ['backlog', 'todo', 'in_progress', 'in_review', 'verifying', 'done'];
    const idx = order.indexOf(task.status);
    if (idx < 0 || idx >= order.length - 1) return;
    void this.changeTaskStatus(order[idx + 1]);
  }
  quickCompleteTask(): void {
    void this.changeTaskStatus('done');
  }
  // A-22: 任务列表/看板「快速完成」勾选（toggle done / 重新打开）
  // 从组件权威状态 this.tasks() 读取最新状态，避免模板 item 闭包在 refresh() 重渲染后过期
  async toggleTaskComplete(id: number): Promise<void> {
    const task = this.tasks().find((t) => t.id === id);
    if (!task) return;
    const target: Status = task.status === 'done' ? 'todo' : 'done';
    if (task.status === target) return;
    await this.run(
      target === 'done' ? '已标记为完成' : '已重新打开',
      () => firstValueFrom(this.api.setTaskStatus(id, target)),
    );
  }
  // Epic 33.2: Task 快速复制
  async duplicateTask(id: number): Promise<void> {
    const task = this.tasks().find((t) => t.id === id);
    if (!task || !task.story_id) return;
    await this.run(
      '任务已复制',
      () => firstValueFrom(this.api.createTask(task.story_id!, {
        project_id: task.project_id,
        title: task.title + ' (副本)',
        type: task.type,
        priority: task.priority,
        description: task.description,
        labels: task.labels,
      })),
    );
  }

  // Epic 36: Inline task title editing
  startInlineEdit(id: number): void {
    const task = this.tasks().find((t) => t.id === id);
    if (!task) return;
    this.editingTaskId.set(id);
    this.editingTaskTitle.set(task.title);
  }

  private _savingInline = false;
  saveInlineEdit(): void {
    if (this._savingInline) return;
    const id = this.editingTaskId();
    const newTitle = this.editingTaskTitle().trim();
    if (id === null || !newTitle) { this.cancelInlineEdit(); return; }
    // Clear edit state immediately for responsive UI
    this.editingTaskId.set(null);
    this.editingTaskTitle.set('');
    const task = this.tasks().find((t) => t.id === id);
    if (task && task.title !== newTitle) {
      this._savingInline = true;
      const token = localStorage.getItem('agentboard_token');
      const apiUrl = (window as any).AGENTBOARD_API || 'http://127.0.0.1:8000';
      fetch(`${apiUrl}/api/tasks/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ title: newTitle }),
      })
        .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
        .then(() => this.tasks.update((list) => list.map((t) => t.id === id ? { ...t, title: newTitle } : t)))
        .catch(() => {})
        .finally(() => { this._savingInline = false; });
    }
  }

  cancelInlineEdit(): void {
    this.editingTaskId.set(null);
    this.editingTaskTitle.set('');
  }

  quickDeleteTask(): void {
    const task = this.task();
    if (task) void this.remove('task', task.id);
  }

  /* ================= Epic 15: 项目文档维护 ================= */
  docTypeLabel(t: DocumentType): string {
    return { memory: '记忆', plan: '计划', knowledge: '知识', design: '设计' }[t] || t;
  }
  docStatusLabel(s: DocumentStatus): string {
    return { draft: '草稿', in_review: '评审中', approved: '已批准', cancelled: '已取消' }[s] || s;
  }
  readonly docTypes = DOCUMENT_TYPES;
  readonly docStatuses = DOCUMENT_STATUSES;
  epicTitle(eid: number | null): string {
    if (!eid) return '';
    return this.docDetailEpics().find((e) => e.id === eid)?.title || this.epics().find((e) => e.id === eid)?.title || `Epic #${eid}`;
  }
  projectName(pid: number): string {
    return this.projects().find((p) => p.id === pid)?.name || `#${pid}`;
  }
  docVisible(): DocumentItem[] {
    let list = this.documents();
    const q = this.docSearchQuery().trim().toLowerCase();
    if (q) list = list.filter((d) => d.title.toLowerCase().includes(q) || (d.content || '').toLowerCase().includes(q));
    return list;
  }

  /** Project-scoped doc list for the project tab (filters by current project ID). */
  projectDocVisible(): DocumentItem[] {
    const pid = this.project()?.id;
    if (!pid) return [];
    let list = this.documents().filter((d) => d.project_id === pid);
    const type = this.docFilterType();
    if (type) list = list.filter((d) => d.type === type);
    const status = this.docFilterStatus();
    if (status) list = list.filter((d) => d.status === status);
    const q = this.docSearchQuery().trim().toLowerCase();
    if (q) list = list.filter((d) => d.title.toLowerCase().includes(q) || (d.content || '').toLowerCase().includes(q));
    return list;
  }

  async loadDocuments(): Promise<void> {
    const params: Record<string, any> = {};
    if (this.docFilterType()) params['type'] = this.docFilterType();
    if (this.docFilterStatus()) params['status'] = this.docFilterStatus();
    const list = await firstValueFrom(this.api.listDocuments(params));
    this.documents.set(list || []);
  }
  onDocFilterChange(): void {
    void this.loadDocuments();
  }
  onDocSearchChange(value: string): void {
    this.docSearchQuery.set(value);
    void this.loadDocuments();
  }

  async openDocModal(mode: 'create' | 'edit'): Promise<void> {
    if (mode === 'create') {
      const first = this.projects()[0];
      const pid = this.project()?.id ?? first?.id ?? null;
      this.docCreateProjectId.set(pid);
      this.docCreateEpicId.set(null);
      this.docCreateStoryId.set(null);
      this.docCreateTitle.set('');
      this.docCreateType.set('plan');
      this.docCreateContent.set('');
      if (pid) {
        this.docCreateEpics.set(await firstValueFrom(this.api.listEpics(pid)));
        this.docCreateStories.set([]);
      }
      this.docModal.set({ mode: 'create' });
    } else {
      const d = this.docItem();
      if (!d) return;
      this.docEditTitle.set(d.title);
      this.docEditContent.set(d.content);
      this.docEditType.set(d.type);
      this.docEditStatus.set(d.status);
      this.docEditEpicId.set(d.epic_id);
      this.docEditStoryId.set(d.story_id);
      try {
        this.docDetailEpics.set(await firstValueFrom(this.api.listEpics(d.project_id)));
        this.docDetailStories.set(d.epic_id ? await firstValueFrom(this.api.listStories(d.epic_id)) : []);
      } catch {
        /* 关联选项加载失败不阻断编辑 */
      }
      this.docModal.set({ mode: 'edit' });
    }
  }
  closeDocModal(): void {
    this.docModal.set(null);
  }
  async onDocCreateProjectChange(pid: number): Promise<void> {
    this.docCreateProjectId.set(pid);
    this.docCreateEpicId.set(null);
    this.docCreateStoryId.set(null);
    if (pid) {
      this.docCreateEpics.set(await firstValueFrom(this.api.listEpics(pid)));
      this.docCreateStories.set([]);
    }
  }
  async onDocCreateEpicChange(eid: number | null): Promise<void> {
    this.docCreateEpicId.set(eid);
    this.docCreateStoryId.set(null);
    if (eid) {
      this.docCreateStories.set(await firstValueFrom(this.api.listStories(eid)));
    } else {
      this.docCreateStories.set([]);
    }
  }
  async submitDocModal(): Promise<void> {
    const dm = this.docModal();
    if (!dm) return;
    if (dm.mode === 'create') {
      const title = this.docCreateTitle().trim();
      const pid = this.docCreateProjectId();
      if (!title || !pid) { this.notify('请填写标题并选择项目', 'error'); return; }
      try {
        const created = await firstValueFrom(this.api.createDocument({
          project_id: pid,
          title,
          type: this.docCreateType(),
          content: this.docCreateContent(),
          epic_id: this.docCreateEpicId(),
          story_id: this.docCreateStoryId(),
        }));
        this.docModal.set(null);
        this.notify('文档已创建');
        // 追加到列表，使新建文档在当前视图（含项目 Tab）中立即可见
        this.documents.set([created, ...this.documents()]);
        const inProjectTab = this.view() === 'project' && this.activeTab() === 'documents';
        if (inProjectTab) {
          await this.openDocTab(created);
        } else {
          await this.router.navigateByUrl(`/documents/${created.id}`);
        }
      } catch (error) {
        this.notify(`创建失败：${this.message(error)}`, 'error');
      }
    } else {
      const d = this.docItem();
      if (!d) return;
      const title = this.docEditTitle().trim();
      if (!title) { this.notify('标题不能为空', 'error'); return; }
      try {
        const updated = await firstValueFrom(this.api.updateDocument(d.id, {
          title,
          content: this.docEditContent(),
          type: this.docEditType(),
          status: this.docEditStatus(),
          epic_id: this.docEditEpicId(),
          story_id: this.docEditStoryId(),
        }));
        this.docItem.set(updated);
        this.documents.set(this.documents().map((x) => (x.id === updated.id ? updated : x)));
        this.docModal.set(null);
        this.notify('文档已保存');
        setTimeout(() => this.enhanceMermaid(), 80);
      } catch (error) {
        this.notify(`保存失败：${this.message(error)}`, 'error');
      }
    }
  }

  openDocEdit(): void {
    const d = this.docItem();
    if (!d) return;
    this.docEditTitle.set(d.title);
    this.docEditContent.set(d.content);
    this.docEditType.set(d.type);
    this.docEditEpicId.set(d.epic_id);
    this.docEditStoryId.set(d.story_id);
    this.docEditing.set(true);
  }
  cancelDocEdit(): void {
    this.docEditing.set(false);
  }
  /** 在项目 Tab 内打开文档详情：写入当前文档并加载其评论（不走路由）。 */
  async openDocTab(d: DocumentItem): Promise<void> {
    this.docItem.set(d);
    this.docEditTitle.set(d.title);
    this.docEditContent.set(d.content);
    this.docEditType.set(d.type);
    this.docEditStatus.set(d.status);
    this.docEditEpicId.set(d.epic_id);
    this.docEditStoryId.set(d.story_id);
    this.docEditing.set(false);
    this.docCommentPreview.set(false);
    this.docCommentContent.set('');
    try {
      const comments = await firstValueFrom(this.api.listDocumentComments(d.id));
      this.documentComments.set(comments);
    } catch (error) {
      this.documentComments.set([]);
    }
  }
  async onDocEditEpicChange(eid: number | null): Promise<void> {
    this.docEditEpicId.set(eid);
    this.docEditStoryId.set(null);
    if (eid) {
      this.docDetailStories.set(await firstValueFrom(this.api.listStories(eid)));
    } else {
      this.docDetailStories.set([]);
    }
  }
  async saveDocEdit(): Promise<void> {
    const d = this.docItem();
    if (!d) return;
    const title = this.docEditTitle().trim();
    if (!title) { this.notify('标题不能为空', 'error'); return; }
    try {
      const updated = await firstValueFrom(this.api.updateDocument(d.id, {
        title,
        content: this.docEditContent(),
        type: this.docEditType(),
        epic_id: this.docEditEpicId(),
        story_id: this.docEditStoryId(),
      }));
      this.docItem.set(updated);
      this.documents.set(this.documents().map((x) => (x.id === updated.id ? updated : x)));
      this.docEditing.set(false);
      this.notify('文档已保存');
      setTimeout(() => this.enhanceMermaid(), 80);
    } catch (error) {
      this.notify(`保存失败：${this.message(error)}`, 'error');
    }
  }
  async setDocStatus(status: DocumentStatus): Promise<void> {
    const d = this.docItem();
    if (!d) return;
    try {
      const updated = await firstValueFrom(this.api.setDocumentStatus(d.id, status));
      this.docItem.set(updated);
      this.documents.set(this.documents().map((x) => (x.id === updated.id ? updated : x)));
      this.notify(`状态已更新为「${this.docStatusLabel(status)}」`);
    } catch (error) {
      this.notify(`状态更新失败：${this.message(error)}`, 'error');
    }
  }
  deleteDoc(): void {
    const d = this.docItem();
    if (!d) return;
    this.openConfirmation({
      title: '删除文档？',
      message: `确定删除「${d.title}」？该操作不可恢复，关联评论也会一并删除。`,
      confirmLabel: '删除文档',
      tone: 'danger',
    }, async () => {
      await firstValueFrom(this.api.deleteDocument(d.id));
      this.notify('文档已删除');
      this.documents.set(this.documents().filter((x) => x.id !== d.id));
      const inProjectTab = this.view() === 'project' && this.activeTab() === 'documents';
      if (inProjectTab) {
        this.docItem.set(null);
      } else {
        await this.router.navigateByUrl('/documents');
      }
    });
  }

  async addDocComment(event: Event): Promise<void> {
    event.preventDefault();
    const d = this.docItem();
    const content = this.docCommentContent().trim();
    const author = this.commentAuthor();
    if (!d || !content) return;
    try {
      const c = await firstValueFrom(this.api.addDocumentComment(d.id, { author, content }));
      this.documentComments.set([...this.documentComments(), c]);
      this.docCommentContent.set('');
      this.notify('评论已发布');
    } catch (error) {
      this.notify(`评论失败：${this.message(error)}`, 'error');
    }
  }
  async saveDocComment(cid: number, content: string): Promise<void> {
    const trimmed = content.trim();
    if (!trimmed) return;
    try {
      const updated = await firstValueFrom(this.api.updateDocumentComment(cid, { content: trimmed }));
      this.documentComments.set(this.documentComments().map((c) => (c.id === cid ? updated : c)));
      this.notify('评论已更新');
    } catch (error) {
      this.notify(`更新失败：${this.message(error)}`, 'error');
    }
  }
  async deleteDocComment(cid: number): Promise<void> {
    try {
      await firstValueFrom(this.api.deleteDocumentComment(cid));
      this.documentComments.set(this.documentComments().filter((c) => c.id !== cid));
      this.notify('评论已删除');
    } catch (error) {
      this.notify(`删除失败：${this.message(error)}`, 'error');
    }
  }
  toggleDocCommentPreview(): void {
    this.docCommentPreview.set(!this.docCommentPreview());
  }

  /* 轻量 Markdown 渲染（无第三方依赖，离线可用）。
     支持：标题、粗体/斜体、行内/块代码、有序/无序列表、引用、链接、分隔线、表格、以及 ```mermaid 代码块。 */
  renderMarkdown(src: string): string {
    if (!src) return '';
    const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const lines = src.replace(/\r\n/g, '\n').split('\n');
    const out: string[] = [];
    let i = 0;
    const inline = (text: string): string => {
      let t = esc(text);
      t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
      t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      t = t.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
      t = t.replace(/_([^_]+)_/g, '<em>$1</em>');
      t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      return t;
    };
    while (i < lines.length) {
      const line = lines[i];
      // 围栏代码块 / mermaid
      const fence = line.match(/^```(\w*)\s*$/);
      if (fence) {
        const lang = fence[1];
        const buf: string[] = [];
        i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // 跳过结束围栏
        if (lang === 'mermaid') {
          out.push(`<div class="mermaid-block"><div class="mermaid-lang">Mermaid</div><pre class="mermaid">${esc(buf.join('\n'))}</pre></div>`);
        } else {
          out.push(`<pre class="code-block"><code>${esc(buf.join('\n'))}</code></pre>`);
        }
        continue;
      }
      // 标题
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { const lvl = h[1].length; out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`); i++; continue; }
      // 分隔线
      if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(line)) { out.push('<hr/>'); i++; continue; }
      // 引用
      if (/^>\s?/.test(line)) {
        const buf: string[] = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, '')); i++; }
        out.push(`<blockquote>${inline(buf.join(' '))}</blockquote>`);
        continue;
      }
      // 无序列表
      if (/^\s*[-*]\s+/.test(line)) {
        const buf: string[] = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { buf.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++; }
        out.push(`<ul>${buf.join('')}</ul>`);
        continue;
      }
      // 有序列表
      if (/^\s*\d+\.\s+/.test(line)) {
        const buf: string[] = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { buf.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ''))}</li>`); i++; }
        out.push(`<ol>${buf.join('')}</ol>`);
        continue;
      }
      // 表格（首行 | 列 |，次行分隔）
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        const header = line.split('|').filter((c, idx, arr) => idx !== 0 && idx !== arr.length - 1 || (arr.length === 1)).map((c) => c.trim());
        // 简化：按 | 切分并去掉首尾空段
        const parseRow = (r: string) => r.replace(/^\s*\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
        const heads = parseRow(line);
        i += 2;
        const rows: string[] = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(`<tr>${parseRow(lines[i]).map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`); i++; }
        out.push(`<table class="md-table"><thead><tr>${heads.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`);
        continue;
      }
      // 空行
      if (line.trim() === '') { i++; continue; }
      // 段落（合并连续非空行）
      const buf: string[] = [line];
      i++;
      while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,6}\s|>\s?|\s*[-*]\s+|\s*\d+\.\s+|```|\*{3,}|-{3,})/.test(lines[i])) { buf.push(lines[i]); i++; }
      out.push(`<p>${inline(buf.join(' '))}</p>`);
    }
    return out.join('\n');
  }

  // 懒加载 mermaid（CDN），离线时优雅降级为代码块
  private enhanceMermaid(): void {
    const blocks = document.querySelectorAll('pre.mermaid');
    if (blocks.length === 0) return;
    if ((window as any).mermaid) { this._renderMermaid(); return; }
    if (this._docMermaidLoading) return;
    this._docMermaidLoading = true;
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';
    s.onload = () => {
      try { (window as any).mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' }); } catch { /* ignore */ }
      this._renderMermaid();
    };
    s.onerror = () => { /* 离线：保留原始代码块 */ };
    document.head.appendChild(s);
  }
  private _renderMermaid(): void {
    const mermaid = (window as any).mermaid;
    if (!mermaid) return;
    document.querySelectorAll('pre.mermaid').forEach((el, idx) => {
      const code = (el.textContent || '').trim();
      try {
        mermaid.render(`doc-mermaid-${Date.now()}-${idx}`, code).then(({ svg }: any) => {
          const wrap = document.createElement('div');
          wrap.className = 'mermaid-svg';
          wrap.innerHTML = svg;
          el.replaceWith(wrap);
        }).catch(() => { /* 保留代码块 */ });
      } catch { /* ignore */ }
    });
  }

  // Task 604: 通知分组批量操作
  readonly notifGroupCollapsed = signal<Record<string, boolean>>({});
  toggleNotifGroup(type: string): void {
    const cur = { ...this.notifGroupCollapsed() };
    cur[type] = !cur[type];
    this.notifGroupCollapsed.set(cur);
  }
  isNotifGroupCollapsed(type: string): boolean {
    return !!this.notifGroupCollapsed()[type];
  }
  async markGroupRead(type: string): Promise<void> {
    const groups = this.filteredGroupedNotifications();
    for (const n of (groups[type] || [])) {
      if (!n.is_read) await this.markRead(n.id);
    }
    await this.loadNotifications();
  }
  async deleteNotifGroup(type: string): Promise<void> {
    const groups = this.filteredGroupedNotifications();
    for (const n of (groups[type] || [])) {
      await this.deleteNotification(n.id);
    }
  }
  deleteAllNotifications(): void {
    const groups = this.filteredGroupedNotifications();
    const all = Object.values(groups).flat();
    if (all.length === 0) return;
    this.openConfirmation({
      title: '清空全部通知？',
      message: `当前共 ${all.length} 条通知，清空后无法恢复。`,
      confirmLabel: '清空通知',
      tone: 'danger',
    }, async () => {
      for (const n of all) {
        await this.deleteNotification(n.id);
      }
    });
  }
}
