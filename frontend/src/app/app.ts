import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, ViewEncapsulation, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';
import { DOCUMENT } from '@angular/common';
import { Inject } from '@angular/core';
import { filter } from 'rxjs/operators';

import { ApiService, AUTH_EXPIRED_EVENT, OFFLINE_QUEUE_FLUSH_EVENT, perfTracker, ApiMetric, resolveApiBase } from './api.service';
import { LoginComponent } from './login/login';
import { AgentRow, AgentSchedule, ApiKeyInfo, Attachment, AuditLog, Comment, Epic, ItemType, Notification, OverviewStats, Priority, Project, ProjectMember, ProjectStats, ReviewStats, ReviewTimeoutResult, Sprint, SprintStatus, Status, Story, StoryStatusHistoryRow, Task, TaskDependencies, UserProfile, WebhookConfig, DocumentItem, DocumentCommentItem, DocumentFolder, DocumentType, DocumentStatus, DOCUMENT_TYPES, DOCUMENT_STATUSES, ProposalItem, ProposalRoundItem, ProposalQuestionItem, ProposalStatus, PROPOSAL_STATUSES, TicketRequestItem, TicketType } from './models';
import { PaginationComponent } from './pagination/pagination';

type ViewKind = 'home' | 'projects' | 'project' | 'epic' | 'story' | 'task' | 'sprint' | 'documents' | 'document' | 'proposals' | 'proposal' | 'agents' | 'notifications' | 'admin' | 'settings' | 'not-found';
type CreateKind = 'project' | 'epic' | 'story' | 'task';
type ProjectTabKind = 'epics' | 'sprints' | 'backlog' | 'proposals' | 'settings' | 'members' | 'stats' | 'schedules' | 'documents';
type ProjectListKind = 'epics' | 'sprints' | 'backlog' | 'members' | 'schedules';

interface CreateModal {
  kind: CreateKind;
  parentId?: number;
  projectId?: number;
  epicId?: number;
  storyId?: number;
}

type ConfirmationTone = 'danger' | 'warning' | 'info';

// v3.1 / v4.0: 筛选预设 —— 保存当前筛选组合，localStorage 持久化
// v4.0 增强：支持多命名预设 + 默认预设（一键应用）；捕获全量多选 chips 数组（不再仅取首个）
interface FilterPreset {
  id: string;          // 稳定唯一 id
  name: string;        // 预设名称（用户命名）
  isDefault: boolean;  // 是否默认预设（面板提供一键应用）
  statuses: string[];  // 状态多选（当前 UI 单选，最多 1 个）
  priorities: string[];// 优先级多选 chips
  types: string[];     // 类型多选 chips
  assignees: string[]; // 指派人多选 chips（含 'unassigned' 哨兵）
  due: string;         // 截止日期单选分桶（''=全部）
  search: string;
  mineOnly: boolean;
  groupBy: string;     // 分组维度
  sortKey: string;     // 排序维度
  sortOrder: string;   // 排序方向 asc/desc
}

interface ConfirmationDialog {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  tone: ConfirmationTone;
  action: () => Promise<void>;
}

interface PaletteCommand {
  id: string;
  title: string;
  hint?: string;
  keywords?: string;
  category?: 'command' | 'task' | 'project' | 'story' | 'document' | 'epic' | 'sprint' | 'notification' | 'agent' | 'proposal' | 'ticket' | 'schedule';
  run: () => void;
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
  // Epic 117 (Task 995): 首页 Dashboard 单请求聚合统计（跨项目）
  readonly overviewStats = signal<OverviewStats | null>(null);
  readonly epics = signal<Epic[]>([]);
  readonly stories = signal<Story[]>([]);
  readonly tasks = signal<Task[]>([]);
  readonly comments = signal<Comment[]>([]);
  readonly storyComments = signal<Comment[]>([]);
  readonly epicComments = signal<Comment[]>([]);
  readonly sprints = signal<Sprint[]>([]);
  readonly sprint = signal<Sprint | null>(null);
  readonly sprintTasks = signal<Task[]>([]);
  readonly backlogTasks = signal<Task[]>([]);
  readonly sprintBurndown = signal<any>(null);
  readonly project = signal<Project | null>(null);
  readonly epic = signal<Epic | null>(null);
  readonly story = signal<Story | null>(null);
  readonly task = signal<Task | null>(null);
  // Ticket 全流程（2026-08-09）：Agent 池视图 + Story 确认/状态历史
  readonly agents = signal<AgentRow[]>([]);
  readonly agentLoading = signal(false);
  readonly storyStatusHistory = signal<StoryStatusHistoryRow[]>([]);
  readonly showStoryStatusHistory = signal(false);
  readonly confirmingStory = signal(false);
  readonly view = signal<ViewKind>('home');
  readonly loading = signal(true);
  /** Epic 78 (v6.6): 手动刷新进行中标记，用于刷新按钮的加载态与防重复点击 */
  readonly refreshing = signal(false);
  /** Epic 81 (v6.9): 后台自动轮询刷新 */
  readonly autoRefreshSeconds = 30; // 轮询间隔（秒）
  readonly autoRefresh = signal(this.isAutoRefreshEnabled());
  readonly autoRefreshCountdown = signal(this.autoRefreshSeconds); // 距下次自动刷新的倒计时（秒）
  readonly lastSyncedAt = signal<number | null>(null); // 上次成功自动同步的时间戳
  readonly autoRefreshFailing = signal(false); // 连续自动同步失败时置位（用于低调告警点，不打扰式 toast）
  /** Epic 84 (v6.12): 自动同步失败重试计数——每次失败同步（含手动「重试」触发）自增，成功同步归零 */
  readonly autoRefreshAttempts = signal(0);
  /** Epic 83 (v6.11): 自动同步成功瞬时标记——点亮绿点并短暂显示「已同步」轻提示（不每周期打扰） */
  readonly autoSynced = signal(false);
  private autoSyncedTimer: ReturnType<typeof setTimeout> | null = null;
  private autoTimer: ReturnType<typeof setInterval> | null = null;
  readonly error = signal('');
  readonly search = signal('');
  readonly sidebarOpen = signal(window.innerWidth > 800);
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
  // Tab state for epic / story detail+list views
  readonly epicTab = signal<'detail' | 'list'>('detail');
  readonly storyTab = signal<'detail' | 'list'>('detail');
  readonly epicEditOpen = signal(false);
  readonly members = signal<ProjectMember[]>([]);
  readonly notifications = signal<Notification[]>([]);
  readonly unreadCount = signal(0);
  readonly showUserMenu = signal(false);
  readonly projectStats = signal<ProjectStats | null>(null);
  // Epic 122 S4: 评审运营视图（统计 + 超时重派）
  readonly reviewStats = signal<ReviewStats | null>(null);
  readonly reviewStatsLoading = signal(false);
  readonly reviewStatsError = signal('');
  readonly reviewReassignBusy = signal(false);
  readonly reviewReassignResult = signal<ReviewTimeoutResult | null>(null);
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
  // 从文档（仅关联 Epic）新增任务时，供弹窗选择 Story 的选项
  readonly createStoryOptions = signal<Story[]>([]);
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
  readonly docCreateFolderId = signal<number | null>(null);
  // 文档文件夹（Epic 15 增强：文件夹 / 子文件夹 + 拖拽归档）
  readonly docFolders = signal<DocumentFolder[]>([]);
  /** 当前所在文件夹 id；null = 根目录 */
  readonly docFolderId = signal<number | null>(null);
  /** 新建 / 重命名文件夹弹窗 */
  readonly docFolderModal = signal<{ mode: 'create' | 'rename'; folderId?: number; parentId?: number | null } | null>(null);
  readonly docFolderName = signal('');
  /** 拖拽状态：当前拖拽对象（仅本应用内部拖拽） */
  readonly docDrag = signal<{ kind: 'document' | 'folder'; id: number } | null>(null);
  /** 拖拽悬停高亮的 drop 目标（null=无，'root'=根目录） */
  readonly docDropId = signal<number | 'root' | null>(null);
  /** drop 处理防重入（drop 事件在嵌套 drop 目标间冒泡会触发多次） */
  private _docDropBusy = false;

  /* ---------- Epic 96 P0: Proposal 澄清回路 —— 问答工作台 ---------- */
  readonly proposals = signal<ProposalItem[]>([]);
  readonly proposalItem = signal<ProposalItem | null>(null);
  readonly proposalRounds = signal<ProposalRoundItem[]>([]);
  readonly proposalFilterStatus = signal<ProposalStatus | ''>('');
  readonly proposalSearchQuery = signal('');
  /** 本地草稿：questionId -> 用户尚未提交的答案文本 */
  readonly proposalDrafts = signal<Record<number, string>>({});
  /** 本地草稿：questionId -> 是否标记「不确定」 */
  readonly proposalUnsure = signal<Record<number, boolean>>({});
  /** 单条保存 / 整轮提交进行中的问题 id 集合，用于禁用按钮防重复提交 */
  readonly proposalSaving = signal<Set<number>>(new Set<number>());
  readonly proposalSubmitting = signal(false);
  // 新建提案弹窗
  readonly proposalModalOpen = signal(false);
  readonly proposalNewTitle = signal('');
  readonly proposalNewContent = signal('');
  readonly proposalNewProjectId = signal<number | null>(null);
  readonly proposalStatuses = PROPOSAL_STATUSES;
  // 详情页 Tab 切换 + 轮次详情弹窗（2026-08-09 布局重构）
  readonly proposalTab = signal<'info' | 'qa'>('info');
  readonly proposalRoundDetail = signal<ProposalRoundItem | null>(null);
  // Proposal → Ticket 异步转化（文档 #59，2026-08-08）
  readonly proposalTicketRequests = signal<TicketRequestItem[]>([]);
  readonly ticketType = signal<TicketType>('story');
  readonly ticketEpicId = signal<number | null>(null);
  readonly ticketStoryId = signal<number | null>(null);
  readonly ticketEpics = signal<Epic[]>([]);
  readonly ticketStories = signal<Story[]>([]);
  readonly ticketGenerating = signal(false);
  private _ticketPollTimer: any = null;

  // 计划（Sprint）创建弹窗
  readonly sprintModalOpen = signal<number | null>(null);
  readonly sprintName = signal('');
  readonly sprintType = signal<'cron' | 'once'>('cron');
  readonly sprintCron = signal('');
  readonly sprintAgent = signal('');  // Story 106：绑定 Agent（空 = 系统默认）
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
    proposals: false,
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
    proposals: false,
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
    proposals: '',
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
  readonly bulkAssignSearch = signal<string>(''); // v5.1 批量指派：成员搜索关键字
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
  // Epic 67 v5.4: 命令面板 (Ctrl/Cmd+K)
  readonly paletteOpen = signal(false);
  readonly paletteQuery = signal('');
  readonly paletteIndex = signal(0);
  // Epic 69 v5.6: 命令面板接入后端搜索
  readonly paletteSearching = signal(false);
  readonly paletteTaskResults = signal<PaletteCommand[]>([]);
  readonly paletteProjectResults = signal<PaletteCommand[]>([]);
  readonly paletteStoryResults = signal<PaletteCommand[]>([]);
  readonly paletteDocumentResults = signal<PaletteCommand[]>([]);
  readonly paletteEpicResults = signal<PaletteCommand[]>([]);
  readonly paletteSprintResults = signal<PaletteCommand[]>([]);
  readonly paletteNotificationResults = signal<PaletteCommand[]>([]);
  readonly paletteAgentResults = signal<PaletteCommand[]>([]);
  readonly paletteProposalResults = signal<PaletteCommand[]>([]);
  readonly paletteTicketResults = signal<PaletteCommand[]>([]);
  readonly paletteScheduleResults = signal<PaletteCommand[]>([]);
  private paletteDebounceTimer: ReturnType<typeof setTimeout> | null = null;
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
  // Task 708: 常驻性能徽标（持续显示页面加载 / API 延迟）
  readonly showPerfBadge = signal<boolean>(localStorage.getItem('agentboard_perf_badge') !== 'off');
  readonly perfHealthLevel = computed<'good' | 'warn' | 'bad'>(() => {
    const avg = this.avgApiDuration();
    const rate = this.apiSuccessRate();
    if (avg > 0 && avg <= 300 && rate >= 95) return 'good';
    if (avg > 1000 || rate < 80) return 'bad';
    return 'warn';
  });
  // Task 721: 看板列折叠状态
  readonly collapsedColumns = signal<Set<string>>(new Set(
    JSON.parse(localStorage.getItem('agentboard_collapsed_cols') || '[]')
  ));
  // v6.2: 看板子分组折叠状态（key = status + '::' + subgroupKey；flat 分组 key='' 不参与）
  readonly collapsedSubgroups = signal<Set<string>>(new Set(
    JSON.parse(localStorage.getItem('agentboard_collapsed_subgroups') || '[]')
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
    'in_design',
    'design_pending_review',
    'design_review_approved',
    'in_progress',
    'in_review',
    'final_review',
    'verifying',
    'done',
    'blocked',
  ];
  readonly priorities: Priority[] = ['highest', 'high', 'medium', 'low', 'lowest'];
  // v5.5: 批量修改任务类型 —— 任务类型枚举（与分组/类型筛选一致）
  readonly taskTypes: string[] = ['task', 'bug', 'test_execution', 'design'];

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
  // Story 199: 可折叠「编辑 Story」表单（描述）
  readonly editStoryDesc = signal('');
  readonly editStoryOpen = signal(false);
  // Epic 123: Story 编辑弹窗内可切换 needs_design（是否需要设计评审段）
  readonly editNeedsDesign = signal(true);
  readonly savingStory = signal(false);
  readonly activeFilterCount = computed(() => this.filterPriorities().length + this.filterTypes().length + this.filterAssignees().length + (this.filterStatus() ? 1 : 0) + (this.filterDueDate() ? 1 : 0) + (this.labelFilter() ? 1 : 0) + (this.filterMineOnly() ? 1 : 0));
  // Epic 34 (v2.3): 工具条「清除全部筛选」按钮显隐 —— 搜索框非空或任一筛选活跃时显示
  readonly showClearAll = computed(() => this.taskSearchQuery().trim() !== '' || this.activeFilterCount() > 0);
  // Epic 76 (v6.3): 看板/列表视图「激活筛选条件」可视化 chips 条 —— 展示当前生效的筛选，点击单条即可移除
  readonly activeFilterChips = computed<{ key: string; label: string }[]>(() => {
    const chips: { key: string; label: string }[] = [];
    if (this.filterStatus()) chips.push({ key: 'status', label: '状态 · ' + this.statusLabel(this.filterStatus()) });
    for (const p of this.filterPriorities()) chips.push({ key: 'priority:' + p, label: this.priorityLabel(p) });
    for (const t of this.filterTypes()) chips.push({ key: 'type:' + t, label: this.typeLabel(t) });
    for (const a of this.filterAssignees()) chips.push({ key: 'assignee:' + a, label: '指派 · ' + (a === 'unassigned' ? '未指派' : this.getAssigneeName(Number(a))) });
    if (this.filterDueDate()) chips.push({ key: 'due', label: '截止 · ' + (this.dueBucketLabels[this.filterDueDate()] || this.filterDueDate()) });
    if (this.labelFilter()) chips.push({ key: 'label', label: '标签 · ' + this.labelFilter() });
    if (this.filterMineOnly()) chips.push({ key: 'mine', label: '指派给我' });
    const q = this.taskSearchQuery().trim();
    if (q) chips.push({ key: 'search', label: '搜索 · ' + q });
    return chips;
  });

  // Epic 76 (v6.3): 移除单条激活筛选（供 chips 条的 ✕ 使用）
  clearFilterChip(key: string): void {
    if (key === 'status') this.setQuickStatus('');
    else if (key.startsWith('priority:')) this.filterPriorities.set(this.filterPriorities().filter((p) => p !== key.slice(9)));
    else if (key.startsWith('type:')) this.filterTypes.set(this.filterTypes().filter((t) => t !== key.slice(5)));
    else if (key.startsWith('assignee:')) this.filterAssignees.set(this.filterAssignees().filter((a) => a !== key.slice(9)));
    else if (key === 'due') this.filterDueDate.set('');
    else if (key === 'label') this.labelFilter.set('');
    else if (key === 'mine') { this.filterMineOnly.set(false); try { localStorage.removeItem('agentboard_filter_mine'); } catch { /* ignore */ } }
    else if (key === 'search') this.taskSearchQuery.set('');
  }
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
    const counts: Record<string, number> = { backlog: 0, todo: 0, in_design: 0, design_pending_review: 0, design_review_approved: 0, in_progress: 0, in_review: 0, final_review: 0, verifying: 0, done: 0, blocked: 0 };
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
      if (!Array.isArray(arr)) return [];
      return arr.map((p: any, idx: number) => this.migratePreset(p, idx));
    } catch { return []; }
  }
  // v4.0: 兼容 v3.1 旧结构（单值 status/priority/type/assignee 字段）迁移到新数组结构
  private migratePreset(p: any, idx: number): FilterPreset {
    return {
      id: p.id || `preset-${Date.now()}-${idx}`,
      name: p.name || `预设${idx + 1}`,
      isDefault: !!p.isDefault,
      statuses: Array.isArray(p.statuses) ? p.statuses : (p.status ? [p.status] : []),
      priorities: Array.isArray(p.priorities) ? p.priorities : (p.priority ? [p.priority] : []),
      types: Array.isArray(p.types) ? p.types : (p.type ? [p.type] : []),
      assignees: Array.isArray(p.assignees) ? p.assignees : (p.assignee ? [p.assignee] : []),
      due: p.due || '',
      search: p.search || '',
      mineOnly: !!p.mineOnly,
      groupBy: p.groupBy || 'none',
      sortKey: p.sortKey || 'created_at',
      sortOrder: p.sortOrder || 'desc',
    };
  }
  // v4.0: 当前默认预设（用于面板「一键应用默认」按钮）
  readonly defaultPreset = computed<FilterPreset | null>(() =>
    this.filterPresets().find((p) => p.isDefault) || null
  );
  // v6.5: 筛选预设「当前激活」高亮 —— 若当前筛选维度与某已保存预设完全一致，则其被标记为活跃
  private sameSet(a: string[], b: string[]): boolean {
    if (a.length !== b.length) return false;
    const sa = [...a].sort();
    const sb = [...b].sort();
    return sa.every((v, i) => v === sb[i]);
  }
  matchesPreset(p: FilterPreset): boolean {
    const curStatus = this.filterStatus() ? [this.filterStatus()!] : [];
    if (!this.sameSet(curStatus, p.statuses)) return false;
    if (!this.sameSet(this.filterPriorities(), p.priorities)) return false;
    if (!this.sameSet(this.filterTypes(), p.types)) return false;
    if (!this.sameSet(this.filterAssignees(), p.assignees)) return false;
    if ((this.filterDueDate() || '') !== (p.due || '')) return false;
    if (this.taskSearchQuery().trim() !== (p.search || '')) return false;
    if (this.filterMineOnly() !== !!p.mineOnly) return false;
    // labelFilter 不在预设捕获范围内；若其处于活跃则视为不匹配，避免误高亮
    if (this.labelFilter()) return false;
    return true;
  }
  readonly activePresetId = computed<string | null>(() =>
    this.filterPresets().find((p) => this.matchesPreset(p))?.id ?? null
  );
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
  // Task 836: 任务列表分组（不分组 / 按状态 / 按类型 / 按负责人 / 按优先级 / 按截止日期）
  readonly taskGroupBy = signal<'none' | 'status' | 'type' | 'assignee' | 'priority' | 'due'>(
    (localStorage.getItem('agentboard_story_group') as 'none' | 'status' | 'type' | 'assignee' | 'priority' | 'due') || 'none'
  );
  // v3.7: 截止日期分桶顺序（逾期→今天→本周→更晚→无截止）
  readonly dueBucketOrder = ['overdue', 'today', 'week', 'later', 'none'];
  readonly dueBucketLabels: Record<string, string> = {
    overdue: '逾期', today: '今天到期', week: '本周内', later: '更晚', none: '无截止日期',
  };
  readonly taskGroupOptions = [
    { key: 'none', label: '不分组' },
    { key: 'status', label: '按状态' },
    { key: 'type', label: '按类型' },
    { key: 'priority', label: '按优先级' },
    { key: 'assignee', label: '按负责人' },
    { key: 'due', label: '按截止日期' },
  ];
  setTaskGroup(v: string): void {
    this.taskGroupBy.set(v as any);
    localStorage.setItem('agentboard_story_group', v);
  }
  taskSortLabel(): string {
    return this.taskSortOptions.find((o) => o.key === this.taskSortKey())?.label || this.taskSortKey();
  }
  taskGroupLabel(): string {
    return this.taskGroupOptions.find((o) => o.key === this.taskGroupBy())?.label || this.taskGroupBy();
  }
  // v4.7: 预设可视化标签 — 在预设列表中展示分组 / 排序维度与筛选计数
  presetGroupLabel(p: FilterPreset): string {
    return this.taskGroupOptions.find((o) => o.key === p.groupBy)?.label || '不分组';
  }
  presetSortLabel(p: FilterPreset): string {
    const opt = this.taskSortOptions.find((o) => o.key === p.sortKey);
    const label = opt?.label || p.sortKey || '创建时间';
    return `${label} ${p.sortOrder === 'asc' ? '↑' : '↓'}`;
  }
  presetFilterCount(p: FilterPreset): number {
    let n = 0;
    if (p.statuses.length) n++;
    if (p.priorities.length) n++;
    if (p.types.length) n++;
    if (p.assignees.length) n++;
    if (p.due) n++;
    if (p.search) n++;
    if (p.mineOnly) n++;
    return n;
  }
  private groupLabel(mode: string, key: string): string {
    if (mode === 'status') return this.statusLabel(key);
    if (mode === 'type') return this.typeLabel(key);
    if (mode === 'priority') return this.priorityLabel(key);
    if (mode === 'due') return this.dueBucketLabels[key] || '无截止日期';
    if (key === '' || key === 'unassigned') return '未指派';
    return this.getAssigneeName(Number(key)) || '未指派';
  }

  // 任务类型中文标签（含新增的 Test Execution / Design）
  typeLabel(type: string): string {
    if (type === 'bug') return 'Bug';
    if (type === 'test_execution') return 'Test Execution';
    if (type === 'design') return 'Design';
    return 'Task';
  }

  // 任务类型短代号（用于小图标圆圈）
  typeGlyph(type: string): string {
    if (type === 'bug') return 'B';
    if (type === 'test_execution') return 'TE';
    if (type === 'design') return 'D';
    return 'T';
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
            : g === 'priority'
              ? (t.priority || 'medium')
              : g === 'due'
                ? this.dueBucket(t)
                : t.assignee_id == null
                  ? 'unassigned'
                  : String(t.assignee_id);
      (buckets[k] ||= []).push(t);
    }
    let keys: string[];
    if (g === 'status') keys = this.statuses.filter((s) => buckets[s]);
    else if (g === 'type') keys = this.taskTypes.filter((k) => buckets[k]);
    else if (g === 'priority') keys = this.priorities.filter((p) => buckets[p]);
    else if (g === 'due') keys = this.dueBucketOrder.filter((b) => buckets[b]);
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
  readonly doneTasks = computed(() => this.overviewStats()?.counts.done_tasks ?? this.tasks().filter((t) => t.status === 'done').length);
  // Epic 117 (Task 995): 首页统计卡数值，overview 优先、整树回退
  readonly statProjects = computed(() => this.overviewStats()?.counts.projects ?? this.projects().length);
  readonly statEpics = computed(() => this.overviewStats()?.counts.epics ?? this.epics().length);
  readonly statStories = computed(() => this.overviewStats()?.counts.stories ?? this.stories().length);
  readonly statTasks = computed(() => this.overviewStats()?.counts.tasks ?? this.tasks().length);
  readonly dashboardStatusChart = computed(() => {
    const definitions = [
      { status: 'backlog', label: '待规划', color: '#94a3b8' },
      { status: 'todo', label: '待办', color: '#3b82f6' },
      { status: 'in_design', label: '设计中', color: '#8b5cf6' },
      { status: 'design_pending_review', label: '设计待评审', color: '#a78bfa' },
      { status: 'design_review_approved', label: '设计已评审', color: '#6366f1' },
      { status: 'in_progress', label: '进行中', color: '#06b6d4' },
      { status: 'in_review', label: '评审中', color: '#8b5cf6' },
      { status: 'final_review', label: '最终评审', color: '#ec4899' },
      { status: 'verifying', label: '验证中', color: '#f59e0b' },
      { status: 'blocked', label: '已阻塞', color: '#ef4444' },
      { status: 'done', label: '已完成', color: '#10b981' },
    ];
    const overview = this.overviewStats();
    const tasks = this.tasks();
    const total = overview ? overview.counts.tasks : tasks.length;
    const statusMap = overview
      ? new Map(overview.status_distribution.map((row) => [row.status, row.count]))
      : null;
    let cursor = 0;
    const segments = definitions
      .map((definition) => {
        const count = statusMap ? (statusMap.get(definition.status) ?? 0) : tasks.filter((task) => task.status === definition.status).length;
        const percent = total ? Math.round((count / total) * 100) : 0;
        const start = cursor;
        cursor += total ? (count / total) * 360 : 0;
        return { ...definition, count, percent, start, end: cursor };
      })
      .filter((segment) => segment.count > 0);
    const gradient = total
      ? `conic-gradient(${segments.map((segment) => `${segment.color} ${segment.start}deg ${segment.end}deg`).join(', ')})`
      : 'conic-gradient(var(--surface-3) 0deg 360deg)';
    return { total, segments, gradient };
  });

  readonly dashboardProjectProgress = computed(() => {
    const overview = this.overviewStats();
    if (overview) {
      // 后端已按 total 降序并含 0 任务项目
      return overview.projects
        .slice(0, 6)
        .map((p) => ({ id: p.id, name: p.name, total: p.total, done: p.done, percent: p.percent }));
    }
    return this.projects()
      .map((project) => {
        const tasks = this.tasks().filter((task) => task.project_id === project.id);
        const done = tasks.filter((task) => task.status === 'done').length;
        return {
          id: project.id,
          name: project.name,
          total: tasks.length,
          done,
          percent: tasks.length ? Math.round((done / tasks.length) * 100) : 0,
        };
      })
      .sort((left, right) => right.total - left.total || left.name.localeCompare(right.name))
      .slice(0, 6);
  });

  readonly dashboardActivity = computed(() => {
    const today = new Date();
    const dateKey = (date: Date): string => [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, '0'),
      String(date.getDate()).padStart(2, '0'),
    ].join('-');
    const overview = this.overviewStats();
    let raw: Array<{ key: string; label: string; count: number }>;
    if (overview) {
      // 后端聚合近 7 日活动（day: YYYY-MM-DD, count）
      raw = overview.activity_7d.map((row) => ({
        key: row.day,
        label: new Date(`${row.day}T00:00:00`).toLocaleDateString('zh-CN', { weekday: 'short' }),
        count: row.count,
      }));
    } else {
      raw = Array.from({ length: 7 }, (_, index) => {
        const date = new Date(today);
        date.setHours(0, 0, 0, 0);
        date.setDate(today.getDate() - (6 - index));
        const key = dateKey(date);
        const count = this.tasks().filter((task) => dateKey(new Date(task.updated_at)) === key).length;
        return { key, label: date.toLocaleDateString('zh-CN', { weekday: 'short' }), count };
      });
    }
    const max = Math.max(1, ...raw.map((point) => point.count));
    const data = raw.map((point, index) => ({
      ...point,
      x: index * 100,
      y: Math.round(125 - (point.count / max) * 90),
    }));
    const points = data.map((point) => `${point.x},${point.y}`).join(' ');
    return {
      data,
      points,
      areaPoints: `0,140 ${points} 600,140`,
      total: raw.reduce((sum, point) => sum + point.count, 0),
    };
  });
  // Epic 34.1: 任务列表汇总栏（总数/完成率/状态分布堆叠条）
  readonly taskListSummary = computed(() => {
    const list = this.tasks();
    const total = list.length;
    const done = list.filter((t) => t.status === 'done').length;
    const inProgress = list.filter((t) => ['in_design', 'design_pending_review', 'design_review_approved', 'in_progress', 'in_review', 'final_review', 'verifying'].includes(t.status)).length;
    const rate = total === 0 ? 0 : Math.round((done / total) * 100);
    const segments = this.statuses
      .map((st) => ({ status: st, count: list.filter((t) => t.status === st).length }))
      .filter((seg) => seg.count > 0);
    return { total, done, inProgress, rate, segments };
  });

  private routeSub?: Subscription;
  private routeLoadGeneration = 0;
  private toastTimer?: ReturnType<typeof setTimeout>;
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
    // Task 708: 准确的页面加载时间（Navigation Timing API；loadEventEnd 为完整加载耗时）
    const navEntry = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    this.pageLoadTime.set(navEntry && navEntry.loadEventEnd > 0 ? navEntry.loadEventEnd : performance.now());
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
      .subscribe(() => {
        // Epic 59 (v4.6): 路由加载完成后自动应用默认筛选预设（loadRoute 内部会在开头 clearFilters，
        // 故必须在 .then 中、clear 之后应用；defaultPresetApplied 保证仅应用一次，不覆盖后续手动筛选/导航）
        void this.loadRoute().then(() => this.applyDefaultPresetOnLoad());
      });
    void this.loadRoute();
    // Task 401: 通知轮询（每 60s）
    this.notifTimer = setInterval(() => {
      if (this.authVisible()) return;
      void this.loadNotifications();
    }, 60000);
    // Epic 81 (v6.9): 后台自动刷新轮询（若用户偏好已开启则沿用，默认关闭）
    if (this.autoRefresh()) this.startAutoTimer();
    // Story 21.4: 初始化时更新离线队列计数
    try {
      const queue = JSON.parse(localStorage.getItem('agentboard_offline_queue') || '[]');
      this.offlineQueueCount.set(queue.length);
    } catch { this.offlineQueueCount.set(0); }
    // Epic 26 Task 702: 加载搜索历史记录
    this.loadSearchHistory();
    // Task 716/711/815/817: 全局快捷键 - '?' 键打开快捷键帮助，Ctrl+A 全选，Del 删除选中，/ 聚焦搜索，←→ 导航
    window.addEventListener('keydown', (e: KeyboardEvent) => {
      // Epic 67 v5.4: Ctrl/Cmd+K 切换命令面板（全局，优先于其它快捷键）
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        this.togglePalette();
        return;
      }
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
    this.reviewStats.set(null);
    this.reviewStatsError.set('');
    this.reviewReassignResult.set(null);
    this.schedules.set([]);
    this.documents.set([]);
    this.docItem.set(null);
    this.documentComments.set([]);
    this.proposals.set([]);
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
      proposals: false,
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
      proposals: false,
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
      proposals: '',
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
        const [stats, reviewStats] = await Promise.all([
          firstValueFrom(this.api.getProjectStats(projectId)),
          // Epic 122 S4: 评审运营视图（S3 M2 后端 /api/review-stats）；404/未支持时降级为 null
          firstValueFrom(this.api.getReviewStats(projectId)).catch(() => null),
        ]);
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.projectStats.set(stats);
        this.reviewStats.set(reviewStats);
      } else if (tab === 'schedules') {
        const schedules = await firstValueFrom(this.api.listSchedules(projectId));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.schedules.set(schedules);
      } else if (tab === 'documents') {
        const [docs, folders] = await Promise.all([
          firstValueFrom(this.api.listDocuments({ project_id: projectId })),
          firstValueFrom(this.api.listDocumentFolders({ project_id: projectId })),
        ]);
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.documents.set(docs || []);
        this.docFolders.set(folders || []);
        this.docFolderId.set(null);
      } else if (tab === 'proposals') {
        const proposals = await firstValueFrom(this.api.listProposals({ project_id: projectId, limit: 200 }));
        if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
        this.proposals.set(Array.isArray(proposals) ? proposals : []);
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

  /** Epic 117 S3 (Task 997)：项目页 Epic 进度数据加载并发治理 ——
   *  两级全量 Promise.all 改为 parallelMap 分片（≤6），避免瞬时请求风暴与「一损俱损」（单项失败跳过）。 */
  private async loadEpicProgressData(projectId: number, epics: Epic[], generation: number): Promise<void> {
    try {
      const stories = (
        await this.parallelMap(epics, 6, (epic) => firstValueFrom(this.api.listStories(epic.id)))
      ).flat();
      if (!this.isCurrentProjectTabRequest(projectId, generation)) return;
      this.stories.set(stories);
      const tasks = (
        await this.parallelMap(stories, 6, (story) => firstValueFrom(this.api.listTasks(story.id)))
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

  private async loadRoute(skeleton: boolean = true): Promise<void> {
    // 未登录时不加载任何业务数据，由独立登录页接管
    if (this.authVisible()) return;
    // 文档 #59：导航离开后停止 ticket 生成轮询，避免轮询回调用旧 proposalId
    // 覆盖 proposalItem，导致打开其他 Proposal 时被「正在生成 ticket」的提案抢回
    this.stopTicketPolling();
    const generation = ++this.routeLoadGeneration;
    // Epic 78 (v6.6): 手动刷新时 skeleton=false，保留当前内容，仅由刷新按钮显示加载态
    if (skeleton) this.loading.set(true);
    this.error.set('');
    const path = this.router.url.split('?')[0].replace(/^\//, '');
    const [kind = '', rawId, section = '', rawChildId = ''] = path.split('/');
    const id = Number(rawId);
    const childId = Number(rawChildId);
    // 已登录用户直接访问 /login 时回首页
    if (kind === 'login') {
      await this.router.navigateByUrl('/');
      return;
    }
    try {
      await this.loadProjects();
      if (generation !== this.routeLoadGeneration) return;
      this.syncRecentProjects();
      this.syncFavorites();
      if (!kind) {
        this.view.set('home');
        await this.loadDashboard(generation);
      } else if (kind === 'projects') {
        this.view.set('projects');
      } else if (kind === 'project' && id > 0) {
        this.view.set('project');
        const projectTab: ProjectTabKind = section === 'proposals'
          ? 'proposals'
          : section === 'documents'
            ? 'documents'
            : section === 'schedules'
              ? 'schedules'
              : 'epics';
        this.activeTab.set(projectTab);
        this.resetProjectListPages();
        this.resetProjectTabs();
        const project = await firstValueFrom(this.api.getProject(id));
        this.project.set(project);
        this.trackRecentProject(project);
        if (projectTab === 'documents' && childId > 0) {
          await this.loadProjectTab(projectTab, id);
          const doc = this.documents().find((item) => item.id === childId);
          if (doc) await this.openDocTab(doc);
        } else {
          void this.loadProjectTab(projectTab, id);
        }
      } else if (kind === 'epic' && id > 0) {
        this.view.set('epic');
        this.epicTab.set('detail');
        const [epic, stories, epicComments] = await Promise.all([
          firstValueFrom(this.api.getEpic(id)),
          firstValueFrom(this.api.listStories(id)),
          firstValueFrom(this.api.listEpicComments(id)),
        ]);
        this.epic.set(epic);
        this.stories.set(stories);
        this.epicComments.set(epicComments);
        this.project.set(await firstValueFrom(this.api.getProject(epic.project_id)));
      } else if (kind === 'story' && id > 0) {
        this.view.set('story');
        this.storyTab.set('detail');
        this.storyTaskPage.set(1);
        // 防止全局搜索词 / 其他视图的筛选条件泄漏到 Story 任务列表导致空白
        this.search.set('');
        this.clearFilters();
        const story = await firstValueFrom(this.api.getStory(id));
        this.story.set(story);
        // 分页加载 story 任务，确保只属于当前 story
        await this.loadStoryTasks(id, 1);
        const [epic, storyComments] = await Promise.all([
          firstValueFrom(this.api.getEpic(story.epic_id)),
          firstValueFrom(this.api.listStoryComments(id)),
        ]);
        this.epic.set(epic);
        this.storyComments.set(storyComments);
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
        setTimeout(() => this.enhanceMermaid(), 80);
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
      } else if (kind === 'notifications') {
        if (!localStorage.getItem('agentboard_token')) {
          await this.router.navigateByUrl('/login');
          return;
        }
        this.view.set('notifications');
        await this.loadNotifications();
      } else if (kind === 'documents') {
        if (id > 0) {
          const doc = await firstValueFrom(this.api.getDocument(id));
          await this.router.navigateByUrl(`/project/${doc.project_id}/documents/${doc.id}`);
          return;
        } else {
          await this.router.navigateByUrl('/projects');
          return;
        }
      } else if (kind === 'proposals') {
        // Epic 96 P0: Proposal 澄清回路 —— 列表 / 问答工作台
        if (!localStorage.getItem('agentboard_token')) {
          this.router.navigateByUrl('/login');
          return;
        }
        if (id > 0) {
          this.view.set('proposal');
          await this.loadProposalDetail(id);
          const p = this.proposalItem();
          if (p) {
            if (!this.projects().length) await this.loadProjects();
            this.project.set(await firstValueFrom(this.api.getProject(p.project_id)));
          }
        } else {
          await this.router.navigateByUrl('/projects');
          return;
        }
      } else {
        this.view.set('not-found');
      }
    } catch (error) {
      if (generation !== this.routeLoadGeneration) return;
      const status = (error as Error & { status?: number })?.status;
      // 403（无权访问） / 404（项目不存在）→ toast 提示并回首页
      if (status === 403 || status === 404) {
        this.notify(`访问受限：${this.message(error)}`, 'error');
        await this.router.navigateByUrl('/');
        return;
      }
      this.error.set(this.message(error));
    } finally {
      // Epic 78 (v6.6): 手动刷新（skeleton=false）时不切换骨架屏
      if (skeleton && generation === this.routeLoadGeneration) this.loading.set(false);
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

  private async loadDashboard(generation: number = this.routeLoadGeneration): Promise<void> {
    // 阶段一（Epic 117 / Task 995）：单请求聚合统计秒出 —— 统计卡/图表不再等整树
    try {
      const overview = await firstValueFrom(this.api.getOverview());
      if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
      this.overviewStats.set(overview);
    } catch {
      // overview 失败不阻断：整树回退仍在下方执行
    }
    // 阶段二：后台整树加载填充 epics/stories/tasks 全局信号（搜索/跳转等依赖），不阻塞首屏
    void this.loadDashboardFullTree(generation);
  }

  /** Epic 117 S2 (Task 996)：并发受限 map —— 同一时刻最多 limit 个任务在跑；
   *  单项失败跳过（成功项保留、按输入顺序），避免全量 Promise.all 的瞬时并发风暴与「一损俱损」。 */
  private async parallelMap<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
    if (!items.length) return [];
    const results: (R | undefined)[] = new Array(items.length);
    let idx = 0;
    const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
      while (idx < items.length) {
        const i = idx++;
        try {
          results[i] = await fn(items[i]);
        } catch {
          // 单项失败跳过，不中断整段加载
        }
      }
    });
    await Promise.all(workers);
    return results.filter((r): r is R => r !== undefined);
  }

  /** 四级整树级联加载（Epics→Stories→Tasks），仅填充全局信号，不参与首页首屏渲染。
   *  Epic 117 S2 (Task 996)：overview 成功时首页统计/图表已由 overview 驱动，
   *  Task 级全量（每 Story 一请求，请求量最大一级）仅作回退/预热 → 跳过；
   *  overview 失败时保留全量回退，保证图表/统计 computed 依赖的 tasks() 信号仍有数据。 */
  private async loadDashboardFullTree(generation: number): Promise<void> {
    try {
      const overviewOk = this.overviewStats() !== null;
      const allEpics = (
        await this.parallelMap(this.projects(), 6, (project) => firstValueFrom(this.api.listEpics(project.id)))
      ).flat();
      if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
      this.epics.set(allEpics);
      const allStories = (
        await this.parallelMap(allEpics, 6, (epic) => firstValueFrom(this.api.listStories(epic.id)))
      ).flat();
      if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
      this.stories.set(allStories);
      // overview 成功 → 统计卡/图表不依赖 tasks()，跳过请求量最大的 Task 级全量
      if (overviewOk) return;
      const allTasks = (
        await this.parallelMap(allStories, 6, (story) => firstValueFrom(this.api.listTasks(story.id)))
      ).flat();
      if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
      // Story 视图使用 loadStoryTasks 独立加载自身任务；非 story 视图才写入全局 tasks()
      if (this.view() !== 'story') this.tasks.set(allTasks);
    } catch {
      // 整树加载失败不影响已渲染的首屏；下次路由刷新会重试
    }
  }

  async refresh(): Promise<void> {
    await this.loadRoute();
  }

  /** Epic 78 (v6.6): 手动刷新当前视图（任务列表/看板）。刷新期间按钮显示加载态并禁用重复触发，保留当前内容不闪骨架屏 */
  async manualRefresh(): Promise<void> {
    if (this.refreshing()) return;
    this.refreshing.set(true);
    try {
      await this.loadRoute(false);
      if (this.error()) {
        // v6.8: 手动刷新失败（网络/服务端异常）。清空 error 以保留当前内容（不渲染「加载失败」横幅），
        // 仅以 toast 提示失败，按钮保持可用以便重试
        const msg = this.error();
        this.error.set('');
        this.notify(msg ? `刷新失败：${msg}` : '刷新失败，请重试', 'error');
      } else {
        // v6.7: 手动刷新成功后给出成功反馈，形成操作闭环（复用既有 toast 基础设施）
        this.notify('视图已刷新', 'success');
      }
    } finally {
      this.refreshing.set(false);
    }
  }

  /** Epic 81 (v6.9): 是否已开启后台自动刷新（偏好持久化于 localStorage） */
  isAutoRefreshEnabled(): boolean {
    return localStorage.getItem('agentboard_auto_refresh') === 'on';
  }

  /** Epic 81 (v6.9): 切换后台自动刷新；开启时重置倒计时并启动轮询，关闭时停表并复位告警 */
  toggleAutoRefresh(): void {
    if (this.autoRefresh()) {
      this.autoRefresh.set(false);
      localStorage.removeItem('agentboard_auto_refresh');
      this.stopAutoTimer();
      this.autoRefreshFailing.set(false);
    } else {
      this.autoRefresh.set(true);
      localStorage.setItem('agentboard_auto_refresh', 'on');
      this.autoRefreshCountdown.set(this.autoRefreshSeconds);
      this.startAutoTimer();
    }
  }

  private startAutoTimer(): void {
    this.stopAutoTimer();
    this.autoTimer = setInterval(() => this.autoTick(), 1000);
  }

  private stopAutoTimer(): void {
    if (this.autoTimer !== null) {
      clearInterval(this.autoTimer);
      this.autoTimer = null;
    }
  }

  /** Epic 81 (v6.9): 每秒心跳——页面不可见时冻结倒计时（不浪费请求），归零时触发一次静默自动同步 */
  private autoTick(): void {
    if (!this.autoRefresh() || document.hidden) return;
    let c = this.autoRefreshCountdown() - 1;
    if (c <= 0) {
      c = this.autoRefreshSeconds;
      void this.autoRefreshTick();
    }
    this.autoRefreshCountdown.set(c);
  }

  /** Epic 81 (v6.9): 静默自动同步——复用 loadRoute(false) 保留当前视图，不弹 toast；
   *  成功则记录 lastSyncedAt 并清除告警，失败则低调置位 failing（与手动刷新 toast 解耦） */
  private async autoRefreshTick(): Promise<void> {
    if (this.refreshing()) return; // 手动刷新或其它自动同步进行中，跳过本拍
    this.refreshing.set(true);
    const wasFailing = this.autoRefreshFailing(); // v6.11: 记录进入本拍前的失败态，用于「恢复」判定
    try {
      await this.loadRoute(false);
      if (this.error()) {
        // 失败：低调置位 failing（粘性，直到一次成功的自动同步才复位）+ v6.12 重试计数自增
        this.autoRefreshFailing.set(true);
        this.autoRefreshAttempts.update(n => n + 1);
      } else {
        this.lastSyncedAt.set(Date.now());
        this.autoRefreshFailing.set(false);
        this.autoRefreshAttempts.set(0); // v6.12: 成功即归零
        this.pulseSynced(); // v6.11: 同步成功瞬时点亮绿点 + 短暂「已同步」轻提示
        // v6.11: 从失败中恢复时给一次成功 toast，与 v6.10 失败提示联动（不每周期打扰）
        if (wasFailing) {
          this.notify('后台已恢复同步', 'success');
        }
      }
    } catch {
      this.autoRefreshFailing.set(true);
      this.autoRefreshAttempts.update(n => n + 1); // v6.12: 异常也计一次重试
    } finally {
      this.refreshing.set(false);
    }
  }

  /** Epic 83 (v6.11): 同步成功瞬时点亮绿点并短暂显示「已同步」轻提示（1.5s 后自动熄灭，避免每周期打扰） */
  private pulseSynced(): void {
    this.autoSynced.set(true);
    if (this.autoSyncedTimer !== null) clearTimeout(this.autoSyncedTimer);
    this.autoSyncedTimer = setTimeout(() => this.autoSynced.set(false), 1500);
  }

  /** Epic 82 (v6.10): 后台自动刷新失败时的一键重试——立即触发一次静默同步并重置倒计时，
   *  给用户明确的可恢复入口；与手动刷新 toast 解耦，重试本身不打扰式提示。
   *  允许在刷新进行中强制重试：先复位刷新态，立即发起一次新同步（旧的在途同步会被取代）。 */
  retryAutoRefresh(): void {
    if (!this.autoRefresh()) return;
    this.autoRefreshCountdown.set(this.autoRefreshSeconds);
    // 若当前恰有同步在途，先复位以便本拍立即生效（避免被 refreshing 守卫跳过）
    if (this.refreshing()) this.refreshing.set(false);
    void this.autoRefreshTick();
  }

  /** Epic 81 (v6.9): 上次同步的相对时间文案，用于低调提示（如「刚刚 / 12s前 / 3分钟前」） */
  lastSyncedLabel(): string {
    const ts = this.lastSyncedAt();
    if (!ts) return '';
    const sec = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (sec < 5) return '刚刚同步';
    if (sec < 60) return `${sec}秒前同步`;
    const min = Math.round(sec / 60);
    if (min < 60) return `${min}分钟前同步`;
    const hr = Math.round(min / 60);
    return `${hr}小时前同步`;
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
    this.stopAutoTimer();
    localStorage.removeItem('agentboard_token');
    localStorage.removeItem('agentboard_user');
    localStorage.removeItem('agentboard_is_admin');
    this.currentUser.set('');
    this.isAdmin.set(false);
    this.isOwner.set(false);
    this.showLogin('login');
    this.notify('已退出登录');
  }

  openCreate(kind: CreateKind, parentId?: number, projectId?: number, ctx?: { epicId?: number; storyId?: number }): void {
    this.modal.set({ kind, parentId, projectId, epicId: ctx?.epicId, storyId: ctx?.storyId });
    if (kind === 'task' && ctx?.epicId) {
      this.api.listStories(ctx.epicId).subscribe((stories) => this.createStoryOptions.set(stories));
    }
  }

  // 在文档关联的 Epic / Story 下新增任务：优先使用 Story，仅关联 Epic 时弹窗选 Story
  addTaskFromDoc(): void {
    const d = this.docItem();
    if (!d) return;
    if (d.story_id) {
      this.openCreate('task', d.story_id, d.project_id);
    } else if (d.epic_id) {
      this.openCreate('task', undefined, d.project_id, { epicId: d.epic_id });
    } else {
      this.notify('该文档尚未关联 Epic / Story，无法在其下新增任务', 'error');
    }
  }

  openCreateSchedule(projectId: number): void {
    this.sprintModalOpen.set(projectId);
    this.sprintName.set('');
    this.sprintType.set('cron');
    this.sprintCron.set('');
    this.sprintAgent.set('');
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
    const agent = this.sprintAgent().trim() || null;
    this.sprintModalOpen.set(null);
    await this.createNewSchedule(pid, title, type, cron, agent);
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
        const needsDesign = data.get('needs_design') !== null;
        await firstValueFrom(
          this.api.createStory(modal.parentId, { title: title.trim(), description, needs_design: needsDesign }),
        );
      } else if (modal.kind === 'task' && modal.projectId) {
        // 从文档（仅关联 Epic）进入时，以弹窗所选 Story 为准；否则沿用 parentId（Story）
        const storyId = modal.epicId ? Number(data.get('story_id')) : modal.parentId;
        if (!storyId) {
          this.notify('请先选择 Story', 'error');
          return;
        }
        await firstValueFrom(
          this.api.createTask(storyId, {
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

  // ---------- Ticket 全流程（2026-08-09）：Story 确认 / 状态历史 / Agent 池 ----------

  /** 用户确认 Story 开始（人工闸门）：backlog → confirmed，触发 agent 自动处理。 */
  async confirmStory(): Promise<void> {
    const story = this.story();
    if (!story || this.confirmingStory()) return;
    this.confirmingStory.set(true);
    try {
      const updated = await firstValueFrom(this.api.confirmStory(story.id));
      this.story.update((s) => (s ? { ...s, status: updated.status } : s));
      this.notify('已确认，Agent 自动处理已启动', 'success');
      await this.loadStoryTasks(updated.id, this.storyTaskPage());
      this.loadStoryStatusHistory();
    } catch (e: any) {
      this.notify(`确认失败：${e?.error?.detail || e?.message || '未知错误'}`, 'error');
    } finally {
      this.confirmingStory.set(false);
    }
  }

  async loadStoryStatusHistory(): Promise<void> {
    const story = this.story();
    if (!story) return;
    try {
      const rows = await firstValueFrom(this.api.storyStatusHistory(story.id));
      this.storyStatusHistory.set(Array.isArray(rows) ? rows : rows?.items || []);
    } catch {
      this.storyStatusHistory.set([]);
    }
  }

  toggleStoryStatusHistory(): void {
    this.showStoryStatusHistory.update((v) => !v);
    if (this.showStoryStatusHistory() && !this.storyStatusHistory().length) {
      this.loadStoryStatusHistory();
    }
  }

  /** 进入 Agent 池视图并加载列表（用户可见自己的 agent 数量与在线状态）。 */
  async goAgents(): Promise<void> {
    this.view.set('agents');
    this.connectAgentWs();
    await this.loadAgents();
  }

  async loadAgents(): Promise<void> {
    this.agentLoading.set(true);
    try {
      const rows = await firstValueFrom(this.api.listAgents());
      this.agents.set(Array.isArray(rows) ? rows : []);
    } catch (e: any) {
      this.notify(`Agent 列表加载失败：${e?.error?.detail || e?.message || ''}`, 'error');
      this.agents.set([]);
    } finally {
      this.agentLoading.set(false);
    }
  }

  agentRoles(a: AgentRow): string[] {
    try {
      const arr = JSON.parse(a.roles || '[]');
      return Array.isArray(arr) ? arr.map(String) : [];
    } catch {
      return [];
    }
  }

  agentCapabilities(a: AgentRow): string[] {
    try {
      const arr = JSON.parse(a.capabilities || '[]');
      return Array.isArray(arr) ? arr.map(String) : [];
    } catch {
      return [];
    }
  }

  // ---------- Agent 配置中心（2026-08-09：前端创建/编辑/删除 + 模型选择） ----------
  readonly agentFormVisible = signal(false);
  readonly agentFormBusy = signal(false);
  readonly agentFormIsEdit = signal(false);
  readonly agentForm = signal({
    agent_id: '',
    name: '',
    cli_command: '',
    model: '',
    roles: '["developer"]',
    enabled: true,
  });

  openAgentForm(a?: AgentRow): void {
    this.agentForm.set({
      agent_id: a?.agent_id || '',
      name: a?.name || '',
      cli_command: a?.cli_command || '',
      model: a?.model || '',
      roles: a?.roles || '["developer"]',
      enabled: a?.enabled ?? true,
    });
    this.agentFormIsEdit.set(!!a);
    this.agentFormVisible.set(true);
  }

  closeAgentForm(): void {
    this.agentFormVisible.set(false);
  }

  setAgentFormField(field: string, value: string | boolean): void {
    this.agentForm.update((f) => ({ ...f, [field]: value }));
  }

  async saveAgentForm(): Promise<void> {
    const f = this.agentForm();
    if (!f.agent_id.trim() || !f.name.trim()) {
      this.notify('agent_id 与名称必填', 'error');
      return;
    }
    this.agentFormBusy.set(true);
    try {
      if (this.agentFormIsEdit()) {
        await firstValueFrom(this.api.updateAgent(f.agent_id, {
          name: f.name, cli_command: f.cli_command, model: f.model,
          roles: f.roles, enabled: f.enabled,
        }));
        this.notify('Agent 配置已更新');
      } else {
        await firstValueFrom(this.api.registerAgent({
          agent_id: f.agent_id, name: f.name, cli_command: f.cli_command,
          model: f.model, roles: f.roles, capabilities: '[]', enabled: f.enabled,
        }));
        this.notify('Agent 已创建');
      }
      this.agentFormVisible.set(false);
      await this.loadAgents();
    } catch (e: any) {
      this.notify(`保存失败：${e?.error?.detail || e?.message || ''}`, 'error');
    } finally {
      this.agentFormBusy.set(false);
    }
  }

  async deleteAgentRow(a: AgentRow): Promise<void> {
    if (!confirm(`删除 Agent「${a.name}」（${a.agent_id}）？`)) return;
    try {
      await firstValueFrom(this.api.deleteAgent(a.agent_id));
      this.notify('Agent 已删除');
      this.removeAgentLocal(a.agent_id);
    } catch (e: any) {
      this.notify(`删除失败：${e?.error?.detail || e?.message || ''}`, 'error');
    }
  }

  async probeAgentNow(a: AgentRow): Promise<void> {
    try {
      const updated = await firstValueFrom(this.api.probeAgent(a.agent_id));
      this.upsertAgent(updated);
      this.notify(`探测完成：${updated.online ? '在线' : '离线'} — ${updated.probe_message || ''}`);
    } catch (e: any) {
      this.notify(`探测失败：${e?.error?.detail || e?.message || ''}`, 'error');
    }
  }

  /** WS 收到的 agent 状态 upsert 到本地列表（无则追加，有则合并）。 */
  upsertAgent(updated: AgentRow): void {
    this.agents.update((list) => {
      const idx = list.findIndex((x) => x.agent_id === updated.agent_id);
      if (idx >= 0) {
        const copy = [...list];
        copy[idx] = { ...copy[idx], ...updated };
        return copy;
      }
      return [updated, ...list];
    });
  }

  removeAgentLocal(agentId: string): void {
    this.agents.update((list) => list.filter((x) => x.agent_id !== agentId));
  }

  // ---------- Agent WebSocket 实时状态（2026-08-09） ----------
  private agentWs: WebSocket | null = null;
  private agentWsRetry = 0;

  connectAgentWs(): void {
    try {
      this.agentWs?.close();
    } catch {
      /* ignore */
    }
    // WS 直连 API 地址（与 REST 同源）：
    // - 注入 AGENTBOARD_API（绝对地址）→ 用其 host（生产 docker 直连 API 端口）
    // - 未注入（同源反代 IIS/nginx）→ 用页面 host（反代需 WS upgrade 头）
    let host = window.location.host;
    let secure = window.location.protocol === 'https:';
    if (this.api.baseUrl) {
      try {
        const u = new URL(this.api.baseUrl);
        host = u.host;
        secure = u.protocol === 'https:';
      } catch {
        /* fallback to page host */
      }
    }
    const proto = secure ? 'wss' : 'ws';
    const token = encodeURIComponent(localStorage.getItem('agentboard_token') || '');
    let url = `${proto}://${host}/ws/agents`;
    if (token) url += `?token=${token}`;
    try {
      const ws = new WebSocket(url);
      this.agentWs = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'snapshot' && Array.isArray(msg.agents)) {
            this.agents.set(msg.agents);
          } else if (msg.type === 'agent_state' && msg.agent) {
            this.upsertAgent(msg.agent);
          } else if (msg.type === 'agent_deleted' && msg.agent_id) {
            this.removeAgentLocal(msg.agent_id);
          }
        } catch {
          /* ignore malformed */
        }
      };
      ws.onclose = () => {
        if (this.view() !== 'agents') return;
        this.agentWsRetry = Math.min(this.agentWsRetry + 1, 6);
        setTimeout(() => this.connectAgentWs(), 1000 * Math.pow(2, this.agentWsRetry - 1));
      };
      ws.onopen = () => {
        this.agentWsRetry = 0;
      };
    } catch {
      /* WS 不可用时静默降级（列表仍由 loadAgents 轮询） */
    }
  }

  disconnectAgentWs(): void {
    try {
      this.agentWs?.close();
    } catch {
      /* ignore */
    }
    this.agentWs = null;
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

  async addStoryComment(event: Event, author: string, content: string): Promise<void> {
    event.preventDefault();
    const story = this.story();
    if (!story || !author.trim() || !content.trim()) return;
    localStorage.setItem('agentboard_comment_author', author.trim());
    try {
      await firstValueFrom(
        this.api.addStoryComment(story.id, { author: author.trim(), content: content.trim() }),
      );
      this.notify('评论已发布');
      this.storyComments.set(await firstValueFrom(this.api.listStoryComments(story.id)));
    } catch (error) {
      this.notify(`评论发布失败：${this.message(error)}`, 'error');
    }
  }

  async addEpicComment(event: Event, author: string, content: string): Promise<void> {
    event.preventDefault();
    const epic = this.epic();
    if (!epic || !author.trim() || !content.trim()) return;
    localStorage.setItem('agentboard_comment_author', author.trim());
    try {
      await firstValueFrom(
        this.api.addEpicComment(epic.id, { author: author.trim(), content: content.trim() }),
      );
      this.notify('评论已发布');
      this.epicComments.set(await firstValueFrom(this.api.listEpicComments(epic.id)));
    } catch (error) {
      this.notify(`评论发布失败：${this.message(error)}`, 'error');
    }
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

  // v5.1: 批量指派面板成员搜索过滤（按用户名匹配，空关键字返回全部）
  filteredBulkMembers(): ProjectMember[] {
    const q = this.bulkAssignSearch().trim().toLowerCase();
    const list = this.members();
    if (!q) return list;
    return list.filter((m) => (m.username || '').toLowerCase().includes(q));
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

  // v5.5: 批量修改任务类型（复用 bulkDuplicate 的逐任务 updateTask 循环模式，零后端契约变更）
  async bulkUpdateType(newType: string): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;
    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在更新类型 0/${ids.length} 个任务…` });
    let ok = 0;
    const failed: string[] = [];
    try {
      for (let i = 0; i < ids.length; i++) {
        const id = ids[i];
        const task = this.tasks().find((t) => t.id === id);
        if (!task) continue;
        try {
          await firstValueFrom(this.api.updateTask(id, { type: newType as ItemType }));
          this.tasks.update((list) => list.map((t) => (t.id === id ? { ...t, type: newType as ItemType } : t)));
          ok++;
        } catch (e) {
          failed.push(String(id));
        }
        this.bulkProgress.set({ current: i + 1, total: ids.length, message: `正在更新类型 ${i + 1}/${ids.length} 个任务…` });
      }
      if (failed.length) {
        this.notify(`已更新 ${ok} 个任务类型为「${this.typeLabel(newType)}」，${failed.length} 个失败`, 'error');
      } else {
        this.notify(`已批量更新 ${ok} 个任务的类型为「${this.typeLabel(newType)}」`);
      }
    } finally {
      this.bulkProgress.set(null);
      this.clearTaskSelection();
      await this.refresh();
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

  showBulkActionPanel(type: 'status' | 'delete' | 'priority' | 'assignee' | 'due' | 'type'): void {
    this.bulkActionTarget.set(type);
    if (type === 'assignee') this.bulkAssignSearch.set('');
  }

  closeBulkActionPanel(): void {
    this.bulkActionTarget.set(null);
    this.bulkAssignSearch.set('');
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
      case 'v':
        // v5.8: 切换列表/看板视图（与命令面板提示一致）
        event.preventDefault();
        this.setBoardMode(!this.boardMode());
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

  openNotificationsTab(): void {
    const url = new URL('/notifications', this.document.baseURI).toString();
    const opened = this.document.defaultView?.open(url, '_blank');
    if (!opened) {
      this.notify('浏览器阻止了新标签页，请允许此站点打开标签页', 'error');
      return;
    }
    opened.opener = null;
    opened.focus();
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

  /* ---------- Review Stats (Epic 122 S4) ---------- */
  async loadReviewStats(projectId: number): Promise<void> {
    this.reviewStatsLoading.set(true);
    this.reviewStatsError.set('');
    try {
      const stats = await firstValueFrom(this.api.getReviewStats(projectId));
      this.reviewStats.set(stats);
    } catch (error) {
      this.reviewStats.set(null);
      this.reviewStatsError.set(this.message(error));
    } finally {
      this.reviewStatsLoading.set(false);
    }
  }

  async triggerReassignTimeout(): Promise<void> {
    if (this.reviewReassignBusy()) return;
    const projectId = this.project()?.id;
    if (projectId == null) return;
    this.reviewReassignBusy.set(true);
    this.reviewReassignResult.set(null);
    try {
      const result = await firstValueFrom(this.api.reassignReviewTimeout(projectId, { timeout_minutes: 30, max_per_run: 20 }));
      this.reviewReassignResult.set(result);
      const re = result.stories_reassigned ?? 0;
      const te = result.tasks_reassigned ?? 0;
      const b = result.blocked ?? 0;
      this.notify(`超时重派完成：Story ${re} 个 / Task ${te} 个 / 置 blocked ${b} 个`);
      // 重派后刷新统计
      await this.loadReviewStats(projectId);
    } catch (error) {
      this.notify(`超时重派失败：${this.message(error)}`, 'error');
    } finally {
      this.reviewReassignBusy.set(false);
    }
  }

  /** 评审人工作量条形图最大值（S4 运营视图） */
  maxReviewerReviewed(rs: ReviewStats): number {
    return rs.by_reviewer.reduce((m, r) => Math.max(m, r.story_reviewed + r.task_reviewed), 0);
  }

  /** 评审人评审总数（Story + Task，S4 运营视图） */
  reviewerReviewed(r: { story_reviewed: number; task_reviewed: number }): number {
    return r.story_reviewed + r.task_reviewed;
  }

  /* ---------- 多数决投票进度（Epic 122 S4 M2） ---------- */

  /** 评审模式可读标签（single=单人评审 / majority=多数决评审） */
  reviewModeLabel(mode?: string): string {
    return mode === 'majority' ? '多数决评审' : '单人评审';
  }

  /** 投票进度条百分比（0..100，quorum 恒 >0 由后端保证） */
  reviewVotePct(row: { cast: number; quorum: number }): number {
    const cast = Number(row?.cast ?? 0);
    const quorum = Number(row?.quorum ?? 0);
    if (!(quorum > 0)) return 0;
    return Math.min(100, Math.round((cast / quorum) * 100));
  }

  /** 投票是否已达法定票数（可结算） */
  reviewVoteReached(row: { cast: number; quorum: number }): boolean {
    const cast = Number(row?.cast ?? 0);
    const quorum = Number(row?.quorum ?? 0);
    return quorum > 0 && cast >= quorum;
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

  async createNewSchedule(projectId: number, title: string, scheduleType: string,
                          cronExpr: string, agent: string | null = null): Promise<void> {
    try {
      await firstValueFrom(this.api.createSchedule(projectId, {
        title,
        schedule_type: scheduleType as 'cron' | 'once',
        cron_expr: cronExpr || undefined,
        agent: agent || undefined,
      } satisfies Partial<AgentSchedule>));
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
    this.updatePerformanceMetrics();
    await this.checkHealth();
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

  // Task 708: 切换常驻性能徽标显隐（持久化到 localStorage）
  togglePerfBadge(): void {
    const next = !this.showPerfBadge();
    this.showPerfBadge.set(next);
    localStorage.setItem('agentboard_perf_badge', next ? 'on' : 'off');
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

  // v6.1: 看板列内按维度子分组（复用 taskGroupBy 选择器与 groupLabel 分桶逻辑，与列表分组一致）
  // 保持状态列（status）为拖拽主轴不变；taskGroupBy='none'/'status' 时退化为当前平铺行为。
  boardSubGroups(status: Status): { key: string; label: string; count: number; items: Task[] }[] {
    const g = this.taskGroupBy();
    const list = this.tasksForStatus(status);
    if (!list.length) return [];
    if (g === 'none' || g === 'status') {
      return [{ key: '', label: '', count: list.length, items: list }];
    }
    const buckets: Record<string, Task[]> = {};
    for (const t of list) {
      const k =
        g === 'type' ? t.type
        : g === 'priority' ? (t.priority || 'medium')
        : g === 'due' ? this.dueBucket(t)
        : t.assignee_id == null ? 'unassigned' : String(t.assignee_id);
      (buckets[k] ||= []).push(t);
    }
    let keys: string[];
    if (g === 'type') keys = this.taskTypes.filter((k) => buckets[k]);
    else if (g === 'priority') keys = this.priorities.filter((p) => buckets[p]);
    else if (g === 'due') keys = this.dueBucketOrder.filter((b) => buckets[b]);
    else keys = Object.keys(buckets).sort((a, b) =>
      this.groupLabel('assignee', a).localeCompare(this.groupLabel('assignee', b), 'zh'));
    return keys.map((k) => ({ key: k, label: this.groupLabel(g, k), count: buckets[k].length, items: buckets[k] }));
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

  // v6.0: 看板列全部折叠 / 全部展开（互补 v5.8 单列折叠）
  readonly allColumnsCollapsed = computed(() => {
    const total = this.statuses.length;
    return total > 0 && this.collapsedColumns().size >= total;
  });
  collapseAllColumns(): void {
    const set = new Set<string>(this.statuses);
    this.collapsedColumns.set(set);
    localStorage.setItem('agentboard_collapsed_cols', JSON.stringify([...set]));
  }
  expandAllColumns(): void {
    this.collapsedColumns.set(new Set<string>());
    localStorage.setItem('agentboard_collapsed_cols', JSON.stringify([]));
  }

  // v6.2: 看板子分组折叠/展开（key = status + '::' + subgroupKey；flat 分组 key='' 不参与折叠）
  private persistCollapsedSubgroups(): void {
    localStorage.setItem('agentboard_collapsed_subgroups', JSON.stringify([...this.collapsedSubgroups()]));
  }
  isSubgroupCollapsed(status: Status, key: string): boolean {
    return this.collapsedSubgroups().has(status + '::' + key);
  }
  toggleSubgroupCollapse(status: Status, key: string): void {
    const collapsed = new Set(this.collapsedSubgroups());
    const k = status + '::' + key;
    if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k);
    this.collapsedSubgroups.set(collapsed);
    this.persistCollapsedSubgroups();
  }
  hasSubgroups(status: Status): boolean {
    return this.boardSubGroups(status).some((g) => !!g.key);
  }
  allSubgroupsCollapsed(status: Status): boolean {
    const groups = this.boardSubGroups(status).filter((g) => !!g.key);
    if (!groups.length) return false;
    return groups.every((g) => this.collapsedSubgroups().has(status + '::' + g.key));
  }
  collapseAllSubgroups(status: Status): void {
    const collapsed = new Set(this.collapsedSubgroups());
    for (const g of this.boardSubGroups(status)) if (g.key) collapsed.add(status + '::' + g.key);
    this.collapsedSubgroups.set(collapsed);
    this.persistCollapsedSubgroups();
  }
  expandAllSubgroups(status: Status): void {
    const collapsed = new Set(this.collapsedSubgroups());
    for (const g of this.boardSubGroups(status)) if (g.key) collapsed.delete(status + '::' + g.key);
    this.collapsedSubgroups.set(collapsed);
    this.persistCollapsedSubgroups();
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
          confirmed: '已确认',
          todo: '待办',
          in_design: '设计中',
          design_pending_review: '设计待评审',
          design_review_approved: '设计已评审',
          in_progress: '进行中',
          in_review: '评审中',
          final_review: '最终评审',
          verifying: '验证中',
          done: '完成',
          blocked: '已阻塞',
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
      { backlog: '#F59E0B', confirmed: '#F59E0B', todo: '#0EA5E9', in_design: '#8B5CF6', design_pending_review: '#A78BFA', design_review_approved: '#6366F1', in_progress: '#5B5BD6', in_review: '#7C3AED', final_review: '#EC4899', verifying: '#0EA5E9', done: '#16A34A', blocked: '#DC2626' } as Record<string, string>
    )[status] || '#94a3b8';
  }

  // Story 199: 状态语义色类（warning/info/primary/violet/sky/success/danger）
  statusSemanticClass(status: string): string {
    return (
      { backlog: 'warning', confirmed: 'warning', todo: 'info', in_design: 'violet', design_pending_review: 'violet', design_review_approved: 'violet', in_progress: 'primary', in_review: 'violet', final_review: 'info', verifying: 'sky', done: 'success', blocked: 'danger' } as Record<string, string>
    )[status] || 'info';
  }

  // Epic 47 (v3.4): 任务列表行内快速状态切换 —— 前端镜像后端 TRANSITIONS 状态机，
  // 仅展示合法的目标状态，调用既有 setTaskStatus 端点，零后端契约变更。
  // Epic 123: 扩展设计评审段与最终评审；needs_design 分支由后端校验兜底。
  readonly statusTransitions: Record<string, string[]> = {
    backlog: ['todo', 'blocked'],
    todo: ['in_design', 'in_progress', 'backlog', 'done', 'blocked'],
    in_design: ['design_pending_review', 'todo', 'blocked'],
    design_pending_review: ['design_review_approved', 'in_design', 'blocked'],
    design_review_approved: ['in_progress', 'in_design', 'blocked'],
    in_progress: ['in_review', 'verifying', 'todo', 'done', 'blocked'],
    in_review: ['done', 'in_progress', 'blocked', 'final_review'],
    final_review: ['done', 'in_review', 'blocked'],
    verifying: ['done', 'in_progress', 'blocked'],
    done: ['in_progress', 'todo', 'blocked'],
    blocked: ['todo', 'in_progress'],
  };
  readonly statusMenuTaskId = signal<number | null>(null);
  readonly statusMenuPos = signal<{ x: number; y: number } | null>(null);
  validNextStatuses(task: Task): string[] {
    return this.statusTransitions[task.status] || [];
  }
  // v3.5: 批量状态变更状态机感知 —— 仅展示「所选任务」状态机交集内的合法目标状态，
  // 避免对部分任务应用非法流转（后端会拒绝，造成部分成功/部分失败）。无交集时返回空数组。
  readonly bulkLegalStatuses = computed<string[]>(() => {
    const ids = this.selectedTasks();
    if (ids.size === 0) return [];
    const selected = this.tasks().filter((t) => ids.has(t.id));
    if (selected.length === 0) return [];
    let common: string[] | null = null;
    for (const t of selected) {
      const next = this.statusTransitions[t.status] || [];
      if (common === null) {
        common = [...next];
      } else {
        common = common.filter((s) => next.includes(s));
      }
    }
    return common ?? [];
  });
  statusMenuTask(): Task | undefined {
    const id = this.statusMenuTaskId();
    if (id == null) return undefined;
    return this.tasks().find((t) => t.id === id) || this.visibleTasks().find((t) => t.id === id);
  }
  openStatusMenu(task: Task, event: Event): void {
    event.stopPropagation();
    event.preventDefault();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    this.statusMenuTaskId.set(task.id);
    this.statusMenuPos.set({ x: rect.left, y: rect.bottom + 4 });
  }
  closeStatusMenu(): void {
    this.statusMenuTaskId.set(null);
    this.statusMenuPos.set(null);
  }
  async quickSetStatus(task: Task, target: string): Promise<void> {
    this.closeStatusMenu();
    if (task.status === target) return;
    try {
      await firstValueFrom(this.api.setTaskStatus(task.id, target));
      this.tasks.update((list) => list.map((t) => (t.id === task.id ? { ...t, status: target as Status } : t)));
      this.notify(`已将「${task.title}」状态更新为「${this.statusLabel(target)}」`);
    } catch {
      this.notify('状态切换失败：该流转不被允许', 'error');
    }
  }

  // v3.8: 任务列表行内快速指派（与 v3.4 行内快速状态切换对称；状态机无关，直接 updateTask assignee_id）
  readonly assignMenuTaskId = signal<number | null>(null);
  readonly assignMenuPos = signal<{ x: number; y: number } | null>(null);
  assignMenuTask(): Task | undefined {
    const id = this.assignMenuTaskId();
    return id == null ? undefined : this.tasks().find((t) => t.id === id);
  }
  async openAssignMenu(task: Task, event: Event): Promise<void> {
    event.stopPropagation();
    event.preventDefault();
    if (this.members().length === 0 && task.project_id) {
      await this.loadMembers(task.project_id);
    }
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    this.assignMenuTaskId.set(task.id);
    this.assignMenuPos.set({ x: rect.left, y: rect.bottom + 4 });
  }
  closeAssignMenu(): void {
    this.assignMenuTaskId.set(null);
    this.assignMenuPos.set(null);
  }
  async quickAssign(task: Task, userId: number | null): Promise<void> {
    this.closeAssignMenu();
    if (task.assignee_id === userId) return;
    const next = userId && userId > 0 ? userId : null;
    try {
      await firstValueFrom(this.api.updateTask(task.id, { assignee_id: next }));
      this.tasks.update((list) => list.map((t) => (t.id === task.id ? { ...t, assignee_id: next } : t)));
      this.notify(`已将「${task.title}」指派给「${next ? this.getAssigneeName(next) : '未指派'}」`);
    } catch {
      this.notify('指派失败，请重试', 'error');
    }
  }

  // v3.9: 任务列表行内快速编辑截止日期（与 v3.4 状态 / v3.8 指派 对称；状态机无关，直接 updateTask due_date）
  readonly dueMenuTaskId = signal<number | null>(null);
  readonly dueMenuPos = signal<{ x: number; y: number } | null>(null);
  dueMenuTask(): Task | undefined {
    const id = this.dueMenuTaskId();
    return id == null ? undefined : this.tasks().find((t) => t.id === id);
  }
  dueMenuInitial(): string {
    const t = this.dueMenuTask();
    if (!t || !t.due_date) return '';
    return (t.due_date as string).slice(0, 10);
  }
  openDueMenu(task: Task, event: Event): void {
    event.stopPropagation();
    event.preventDefault();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    this.dueMenuTaskId.set(task.id);
    this.dueMenuPos.set({ x: rect.left, y: rect.bottom + 4 });
  }
  closeDueMenu(): void {
    this.dueMenuTaskId.set(null);
    this.dueMenuPos.set(null);
  }
  async quickSetDue(task: Task, dateStr: string | null): Promise<void> {
    this.closeDueMenu();
    const next = dateStr && dateStr.trim() ? dateStr.trim() : null;
    if ((task.due_date || '').slice(0, 10) === (next || '')) return;
    try {
      await firstValueFrom(this.api.updateTask(task.id, { due_date: next }));
      this.tasks.update((list) => list.map((t) => (t.id === task.id ? { ...t, due_date: next } : t)));
      this.notify(next ? `已将「${task.title}」截止日期设为 ${next}` : `已清除「${task.title}」的截止日期`);
    } catch {
      this.notify('更新截止日期失败，请重试', 'error');
    }
  }

  // v4.1: 任务列表行内快速修改优先级（与 v3.4 状态 / v3.8 指派 / v3.9 截止日期 对称；状态机无关，直接 updateTask priority）
  readonly priorityMenuTaskId = signal<number | null>(null);
  readonly priorityMenuPos = signal<{ x: number; y: number } | null>(null);
  priorityMenuTask(): Task | undefined {
    const id = this.priorityMenuTaskId();
    return id == null ? undefined : this.tasks().find((t) => t.id === id);
  }
  openPriorityMenu(task: Task, event: Event): void {
    event.stopPropagation();
    event.preventDefault();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    this.priorityMenuTaskId.set(task.id);
    this.priorityMenuPos.set({ x: rect.left, y: rect.bottom + 4 });
  }
  closePriorityMenu(): void {
    this.priorityMenuTaskId.set(null);
    this.priorityMenuPos.set(null);
  }
  async quickSetPriority(task: Task, newPriority: string): Promise<void> {
    this.closePriorityMenu();
    if (task.priority === newPriority) return;
    try {
      await firstValueFrom(this.api.updateTask(task.id, { priority: newPriority as Priority }));
      this.tasks.update((list) => list.map((t) => (t.id === task.id ? { ...t, priority: newPriority as Priority } : t)));
      this.notify(`已将「${task.title}」优先级改为「${this.priorityLabel(newPriority)}」`);
    } catch {
      this.notify('更新优先级失败，请重试', 'error');
    }
  }

  // Epic 55 (v4.2): 任务列表行内快速查看抽屉（Quick View Drawer）
  // 复用既有行内菜单（状态/优先级/指派/截止）与列表数据，零后端契约变更。
  readonly qvTaskId = signal<number | null>(null);
  qvTask(): Task | undefined {
    const id = this.qvTaskId();
    if (id == null) return undefined;
    return this.tasks().find((t) => t.id === id);
  }
  openQuickView(task: Task): void {
    if (this.qvTaskId() === task.id) {
      this.qvTaskId.set(null);
      return;
    }
    this.qvTaskId.set(task.id);
    this.qvCommentDraft.set('');
    // Epic 58 (v4.5): 切换到其它任务时清空残留的行内编辑态
    this.qvEditingTitle.set(false);
    this.qvEditingDesc.set(false);
    void this.qvLoadComments();
  }
  closeQuickView(): void {
    this.qvTaskId.set(null);
    this.qvComments.set([]);
    this.qvCommentDraft.set('');
  }
  // Epic 58 (v4.5): 快速查看抽屉内任务前后导航（Jira/Linear 式 triage）
  qvHasPrev(): boolean {
    const t = this.qvTask();
    if (!t) return false;
    const list = this.visibleTasks();
    const idx = list.findIndex((x) => x.id === t.id);
    return idx > 0;
  }
  qvHasNext(): boolean {
    const t = this.qvTask();
    if (!t) return false;
    const list = this.visibleTasks();
    const idx = list.findIndex((x) => x.id === t.id);
    return idx >= 0 && idx < list.length - 1;
  }
  qvNav(delta: number): void {
    const t = this.qvTask();
    if (!t) return;
    const list = this.visibleTasks();
    const idx = list.findIndex((x) => x.id === t.id);
    if (idx < 0) return;
    const ni = idx + delta;
    if (ni < 0 || ni >= list.length) return;
    this.openQuickView(list[ni]);
  }
  // 抽屉内键盘导航：'[' 上一项 / ']' 下一项（输入框聚焦时不触发）
  onDrawerKeydown(event: KeyboardEvent): void {
    const tgt = event.target as HTMLElement;
    if (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.tagName === 'SELECT') return;
    if (this.qvTaskId() == null) return;
    if (event.key === '[') {
      event.preventDefault();
      this.qvNav(-1);
    } else if (event.key === ']') {
      event.preventDefault();
      this.qvNav(1);
    }
  }
  // 抽屉面包屑：基于已加载的 projects/stories/epics 数组推导（带兜底）
  qvBreadcrumb(): { project: string; epic: string; story: string } {
    const t = this.qvTask();
    if (!t) return { project: '', epic: '', story: '' };
    const project = this.projects().find((p) => p.id === t.project_id);
    const story = t.story_id ? this.stories().find((s) => s.id === t.story_id) : undefined;
    const epic = story ? this.epics().find((e) => e.id === story.epic_id) : undefined;
    return {
      project: project?.name || `项目#${t.project_id}`,
      epic: epic?.title || '',
      story: story?.title || '',
    };
  }
  // 抽屉内展示的子任务进度（复用 Task 811 既有方法）
  qvSubtaskTotal(): number {
    const t = this.qvTask();
    return t ? this.getSubtaskCount(t.id).total : 0;
  }
  qvSubtaskDone(): number {
    const t = this.qvTask();
    return t ? this.getSubtaskCount(t.id).done : 0;
  }
  qvSubtaskPct(): number {
    const t = this.qvTask();
    return t ? this.getSubtaskProgress(t.id) : 0;
  }

  // Epic 56 (v4.3): 快速查看抽屉内联编辑标题与描述（扩展 v4.2；状态机无关，直接 updateTask）
  readonly qvEditingTitle = signal<boolean>(false);
  readonly qvEditTitle = signal<string>('');
  readonly qvEditingDesc = signal<boolean>(false);
  readonly qvEditDesc = signal<string>('');

  startQvEditTitle(): void {
    const t = this.qvTask();
    if (!t) return;
    this.qvEditTitle.set(t.title);
    this.qvEditingTitle.set(true);
  }
  cancelQvEditTitle(): void {
    this.qvEditingTitle.set(false);
  }
  async saveQvTitle(): Promise<void> {
    const t = this.qvTask();
    const v = this.qvEditTitle().trim();
    if (!t || !v) { this.qvEditingTitle.set(false); return; }
    if (v === t.title) { this.qvEditingTitle.set(false); return; }
    try {
      await firstValueFrom(this.api.updateTask(t.id, { title: v }));
      this.tasks.update((list) => list.map((x) => (x.id === t.id ? { ...x, title: v } : x)));
      this.qvEditingTitle.set(false);
      this.notify(`已将标题更新为「${v}」`);
    } catch {
      this.notify('更新标题失败，请重试', 'error');
    }
  }
  startQvEditDesc(): void {
    const t = this.qvTask();
    if (!t) return;
    this.qvEditDesc.set(t.description || '');
    this.qvEditingDesc.set(true);
  }
  cancelQvEditDesc(): void {
    this.qvEditingDesc.set(false);
  }
  async saveQvDesc(): Promise<void> {
    const t = this.qvTask();
    const v = this.qvEditDesc();
    if (!t) { this.qvEditingDesc.set(false); return; }
    if (v === (t.description || '')) { this.qvEditingDesc.set(false); return; }
    try {
      await firstValueFrom(this.api.updateTask(t.id, { description: v }));
      this.tasks.update((list) => list.map((x) => (x.id === t.id ? { ...x, description: v } : x)));
      this.qvEditingDesc.set(false);
      this.notify('描述已更新');
    } catch {
      this.notify('更新描述失败，请重试', 'error');
    }
  }

  // Epic 57 (v4.4): 快速查看抽屉评论区 — 查看任务评论 + 行内快速添加/删除评论（复用现有评论 API）
  readonly qvComments = signal<Comment[]>([]);
  readonly qvCommentDraft = signal<string>('');
  readonly qvLoadingComments = signal<boolean>(false);

  async qvLoadComments(): Promise<void> {
    const t = this.qvTask();
    if (!t) return;
    this.qvLoadingComments.set(true);
    try {
      const list = await firstValueFrom(this.api.listComments(t.id));
      this.qvComments.set(Array.isArray(list) ? list : []);
      setTimeout(() => this.enhanceMermaid(), 80);
    } catch {
      this.qvComments.set([]);
    } finally {
      this.qvLoadingComments.set(false);
    }
  }
  async qvAddComment(): Promise<void> {
    const t = this.qvTask();
    const content = this.qvCommentDraft().trim();
    if (!t || !content) return;
    try {
      await firstValueFrom(this.api.addComment(t.id, { author: this.commentAuthor(), content }));
      this.qvCommentDraft.set('');
      await this.qvLoadComments();
    } catch {
      this.notify('添加评论失败，请重试', 'error');
    }
  }
  async qvDeleteComment(id: number): Promise<void> {
    const t = this.qvTask();
    if (!t) return;
    try {
      await firstValueFrom(this.api.deleteComment(id));
      await this.qvLoadComments();
    } catch {
      this.notify('删除评论失败', 'error');
    }
  }

  // Task 821: 任务类型图标
  taskTypeIcon(type: string): string {
    if (type === 'bug') return '🐛';
    if (type === 'test_execution') return '🧪';
    if (type === 'design') return '🎨';
    return '📋';
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

  /* ---------- Epic 67 v5.4: 命令面板 (Ctrl/Cmd+K) ---------- */
  openPalette(): void {
    this.paletteQuery.set('');
    this.paletteIndex.set(0);
    this.paletteTaskResults.set([]);
    this.paletteProjectResults.set([]);
    this.paletteStoryResults.set([]);
    this.paletteDocumentResults.set([]);
    this.paletteEpicResults.set([]);
    this.paletteSprintResults.set([]);
    this.paletteNotificationResults.set([]);
    this.paletteAgentResults.set([]);
    this.paletteProposalResults.set([]);
    this.paletteTicketResults.set([]);
    this.paletteScheduleResults.set([]);
    this.paletteSearching.set(false);
    this.paletteOpen.set(true);
    setTimeout(() => {
      const el = document.getElementById('paletteInput') as HTMLInputElement | null;
      el?.focus();
    }, 0);
  }

  togglePalette(): void {
    if (this.paletteOpen()) {
      this.closePalette();
    } else {
      this.openPalette();
    }
  }

  closePalette(): void {
    this.paletteOpen.set(false);
    this.paletteQuery.set('');
    this.paletteIndex.set(0);
    this.paletteTaskResults.set([]);
    this.paletteProjectResults.set([]);
    this.paletteStoryResults.set([]);
    this.paletteDocumentResults.set([]);
    this.paletteEpicResults.set([]);
    this.paletteSprintResults.set([]);
    this.paletteNotificationResults.set([]);
    this.paletteAgentResults.set([]);
    this.paletteProposalResults.set([]);
    this.paletteTicketResults.set([]);
    this.paletteScheduleResults.set([]);
    this.paletteSearching.set(false);
  }

  /** Epic 69 v5.6: 命令面板输入框处理函数（含 200ms 防抖触发后端搜索） */
  onPaletteInput(value: string): void {
    this.paletteQuery.set(value);
    this.paletteIndex.set(0);
    if (this.paletteDebounceTimer) {
      clearTimeout(this.paletteDebounceTimer);
      this.paletteDebounceTimer = null;
    }
    const v = value;
    this.paletteDebounceTimer = setTimeout(() => this.paletteRunSearch(v), 200);
  }

  /** Epic 70 v5.7 + Epic 119 v6.13: 实时搜索后端任务/Story/文档/Epic（按关键词）与本地项目（按名称/key），结果写入信号供 computed 合并 */
  paletteRunSearch(q: string): void {
    const query = q.trim();
    if (query.length < 2) {
      this.paletteTaskResults.set([]);
      this.paletteProjectResults.set([]);
      this.paletteStoryResults.set([]);
      this.paletteDocumentResults.set([]);
      this.paletteEpicResults.set([]);
      this.paletteSprintResults.set([]);
      this.paletteNotificationResults.set([]);
      this.paletteAgentResults.set([]);
      this.paletteProposalResults.set([]);
      this.paletteTicketResults.set([]);
      this.paletteScheduleResults.set([]);
      this.paletteSearching.set(false);
      return;
    }
    this.paletteSearching.set(true);
    // 项目：客户端过滤已加载的可见项目池（projects 优先，回退 recentProjects）
    const pool = this.projects().length ? this.projects() : this.recentProjects();
    const pq = query.toLowerCase();
    const projCmds: PaletteCommand[] = pool
      .filter((p) => `${p.name} ${p.key || ''}`.toLowerCase().includes(pq))
      .slice(0, 8)
      .map((p) => ({
        id: `project-${p.id}`,
        title: `项目：${p.name}`,
        hint: p.key || 'Project',
        category: 'project',
        keywords: `project ${p.name} ${p.key || ''}`,
        run: () => { void this.router.navigateByUrl(`/project/${p.id}`); },
      }));
    this.paletteProjectResults.set(projCmds);
    // 任务：后端 /api/tasks?q= 搜索（跨可见项目）
    firstValueFrom(this.api.searchTasks({ q: query, limit: 10 }))
      .then((tasks) => {
        const cmds: PaletteCommand[] = (tasks || []).map((t) => ({
          id: `task-${t.id}`,
          title: `任务 #${t.id}：${(t.title || '').slice(0, 60)}`,
          hint: `${this.projectName(t.project_id)} · ${t.status}`,
          category: 'task',
          keywords: `task ${t.id} ${t.title}`,
          run: () => { void this.router.navigateByUrl(`/task/${t.id}`); },
        }));
        this.paletteTaskResults.set(cmds);
      })
      .catch(() => {
        this.paletteTaskResults.set([]);
      })
      .finally(() => {
        this.paletteSearching.set(false);
      });
    // Story：后端 /api/stories/search 搜索
    firstValueFrom(this.api.searchStories({ q: query, limit: 10 }))
      .then((stories) => {
        const cmds: PaletteCommand[] = (stories || []).map((st) => ({
          id: `story-${st.id}`,
          title: `Story #${st.id}：${(st.title || '').slice(0, 60)}`,
          hint: `Epic #${st.epic_id} · ${st.status}`,
          category: 'story',
          keywords: `story ${st.id} ${st.title}`,
          run: () => { void this.router.navigateByUrl(`/story/${st.id}`); },
        }));
        this.paletteStoryResults.set(cmds);
      })
      .catch(() => {
        this.paletteStoryResults.set([]);
      });
    // 文档：后端 /api/documents?q= 搜索
    firstValueFrom(this.api.listDocuments({ q: query }))
      .then((docs) => {
        const cmds: PaletteCommand[] = (docs || []).map((d) => ({
          id: `document-${d.id}`,
          title: `文档 #${d.id}：${(d.title || '').slice(0, 60)}`,
          hint: `${d.type || 'doc'} · ${d.status || ''}`,
          category: 'document',
          keywords: `document ${d.id} ${d.title}`,
          run: () => { void this.router.navigateByUrl(`/project/${d.project_id}/documents/${d.id}`); },
        }));
        this.paletteDocumentResults.set(cmds);
      })
      .catch(() => {
        this.paletteDocumentResults.set([]);
      });
    // Epic（Epic 119 v6.13）：后端 /api/search/epics 搜索
    firstValueFrom(this.api.searchEpics({ q: query, limit: 10 }))
      .then((epics) => {
        const cmds: PaletteCommand[] = (epics || []).map((e) => ({
          id: `epic-${e.id}`,
          title: `Epic #${e.id}：${(e.title || '').slice(0, 60)}`,
          hint: `${this.projectName(e.project_id)} · ${e.status || ''}`,
          category: 'epic',
          keywords: `epic ${e.id} ${e.title}`,
          run: () => { void this.router.navigateByUrl(`/epic/${e.id}`); },
        }));
        this.paletteEpicResults.set(cmds);
      })
      .catch(() => {
        this.paletteEpicResults.set([]);
      });
    // Sprint（Epic 120 v6.14）：后端 /api/search/sprints 搜索
    firstValueFrom(this.api.searchSprints({ q: query, limit: 10 }))
      .then((sprints) => {
        const cmds: PaletteCommand[] = (sprints || []).map((sp) => ({
          id: `sprint-${sp.id}`,
          title: `Sprint #${sp.id}：${(sp.title || '').slice(0, 60)}`,
          hint: `${this.projectName(sp.project_id)} · ${this.sprintStatusLabel(sp.status || '')}`,
          category: 'sprint',
          keywords: `sprint ${sp.id} ${sp.title}`,
          run: () => { void this.router.navigateByUrl(`/sprint/${sp.id}`); },
        }));
        this.paletteSprintResults.set(cmds);
      })
      .catch(() => {
        this.paletteSprintResults.set([]);
      });
    // 通知（Epic 121 v6.15）：后端 /api/search/notifications 搜索当前用户通知
    firstValueFrom(this.api.searchNotifications({ q: query, limit: 10 }))
      .then((notifs) => {
        const cmds: PaletteCommand[] = (notifs || []).map((n) => ({
          id: `notification-${n.id}`,
          title: `通知 #${n.id}：${(n.title || '').slice(0, 60)}`,
          hint: `${this.notifTypeLabel(n.type)}${n.is_read ? '' : ' · 未读'}${n.link ? ' · ' + n.link : ''}`,
          category: 'notification',
          keywords: `notification tongzhi 通知 ${n.id} ${n.title} ${n.content || ''}`,
          run: () => { void this.openNotification(n); },
        }));
        this.paletteNotificationResults.set(cmds);
      })
      .catch(() => {
        this.paletteNotificationResults.set([]);
      });
    // Agent（Epic 131 v6.16）：后端 /api/search/agents 搜索 Agent 注册表（仅 enabled）
    firstValueFrom(this.api.searchAgents({ q: query, limit: 10 }))
      .then((agts) => {
        const cmds: PaletteCommand[] = (agts || []).map((a) => ({
          id: `agent-${a.id}`,
          title: `Agent ${a.agent_id}：${(a.name || '').slice(0, 40)}`,
          hint: `${a.online ? '在线' : '离线'}${a.probe_message ? ' · ' + a.probe_message.slice(0, 30) : ''}`,
          category: 'agent',
          keywords: `agent zhinen 智能体 ${a.agent_id} ${a.name} ${a.roles || ''}`,
          run: () => { void this.goAgents(); },
        }));
        this.paletteAgentResults.set(cmds);
      })
      .catch(() => {
        this.paletteAgentResults.set([]);
      });
    // Proposal（Epic 132 v6.17）：后端 /api/search/proposals 搜索提案（按可见项目收敛）
    firstValueFrom(this.api.searchProposals({ q: query, limit: 10 }))
      .then((props) => {
        const cmds: PaletteCommand[] = (props || []).map((p) => ({
          id: `proposal-${p.id}`,
          title: `Proposal #${p.id}：${(p.title || '').slice(0, 60)}`,
          hint: `${this.projectName(p.project_id)} · ${this.proposalStatusLabel(p.status)}`,
          category: 'proposal',
          keywords: `proposal ti'an 提案 ${p.id} ${p.title} ${p.content || ''}`,
          run: () => { void this.router.navigateByUrl(`/proposals/${p.id}`); },
        }));
        this.paletteProposalResults.set(cmds);
      })
      .catch(() => {
        this.paletteProposalResults.set([]);
      });
    // Ticket（Epic 133 v6.18）：后端 /api/search/tickets 搜索工单（按提案可见项目收敛）
    firstValueFrom(this.api.searchTicketRequests({ q: query, limit: 10 }))
      .then((tks) => {
        const cmds: PaletteCommand[] = (tks || []).map((t) => ({
          id: `ticket-${t.id}`,
          title: `Ticket #${t.id}：${((t.title || '').slice(0, 60) || `Proposal #${t.proposal_id}`)}`,
          hint: `${this.projectName(t.project_id ?? 0)} · ${this.ticketTypeLabel(t.type)} · ${this.ticketRequestStatusLabel(t.status)}`,
          category: 'ticket',
          keywords: `ticket gongdan 工单 ${t.id} ${t.title || ''} ${t.type} ${t.status}`,
          run: () => { void this.router.navigateByUrl(`/proposals/${t.proposal_id}`); },
        }));
        this.paletteTicketResults.set(cmds);
      })
      .catch(() => {
        this.paletteTicketResults.set([]);
      });
    // Schedule（Epic 134 v6.19）：后端 /api/search/schedules 搜索定时计划（按成员项目收敛）
    firstValueFrom(this.api.searchSchedules({ q: query, limit: 10 }))
      .then((schs) => {
        const cmds: PaletteCommand[] = (schs || []).map((sch) => ({
          id: `schedule-${sch.id}`,
          title: `计划 #${sch.id}：${(sch.title || '').slice(0, 60)}`,
          hint: `${this.projectName(sch.project_id)} · ${sch.schedule_type === 'cron' ? (sch.cron_expr || 'cron') : 'once'}${sch.agent ? ` · ${sch.agent}` : ''}`,
          category: 'schedule',
          keywords: `schedule jihua 定时 计划 调度 ${sch.id} ${sch.title || ''} ${sch.agent || ''} ${sch.schedule_type}`,
          run: () => { void this.router.navigateByUrl(`/project/${sch.project_id}/schedules`); },
        }));
        this.paletteScheduleResults.set(cmds);
      })
      .catch(() => {
        this.paletteScheduleResults.set([]);
      });
  }

  /** 构建命令列表（含基于 recentProjects 的动态命令）。在 computed 内访问以跟踪信号变化。 */
  private buildPaletteCommands(): PaletteCommand[] {
    const cmds: PaletteCommand[] = [
      { id: 'home', title: '首页仪表盘', hint: 'Home', keywords: 'home dashboard shouye 首页 仪表盘', run: () => { void this.router.navigateByUrl('/'); } },
      { id: 'projects', title: '项目列表', hint: 'Projects', keywords: 'projects xiangmu 项目 列表', run: () => { void this.router.navigateByUrl('/projects'); } },
      {
        id: 'documents', title: '当前项目文档', hint: 'Project Docs', keywords: 'project documents wendang 项目 文档 中心',
        run: () => {
          const p = this.project();
          if (p) void this.router.navigateByUrl(`/project/${p.id}/documents`);
          else this.notify('请先进入一个项目，再查看项目文档', 'error');
        },
      },
      {
        id: 'proposals', title: '当前项目提案', hint: 'Project Proposals',
        keywords: 'project proposals xuqiu tian 项目 需求 提案 澄清 问答',
        run: () => {
          const p = this.project();
          if (p) void this.router.navigateByUrl(`/project/${p.id}/proposals`);
          else this.notify('请先进入一个项目，再查看项目提案', 'error');
        },
      },
      { id: 'settings', title: '设置', hint: 'Settings', keywords: 'settings shezhi 设置 个人', run: () => { void this.router.navigateByUrl('/settings'); } },
      {
        id: 'new-task', title: '新建任务', hint: 'Task', keywords: 'new task xinjian 新建 任务',
        run: () => {
          const s = this.story();
          const p = this.project();
          if (s) { this.openCreate('task', s.id, p?.id); }
          else { this.notify('请在 Story 视图中新建任务', 'error'); }
        },
      },
      {
        id: 'new-story', title: '新建 Story', hint: 'Story', keywords: 'new story xinjian 新建 故事',
        run: () => {
          const e = this.epic();
          const p = this.project();
          if (e) { this.openCreate('story', e.id, p?.id); }
          else { this.notify('请在 Epic 视图中新建 Story', 'error'); }
        },
      },
      {
        id: 'new-epic', title: '新建 Epic', hint: 'Epic', keywords: 'new epic xinjian 新建 史诗',
        run: () => {
          const p = this.project();
          if (p) { this.openCreate('epic', undefined, p.id); }
          else { this.notify('请在项目视图中新建 Epic', 'error'); }
        },
      },
      { id: 'density', title: '切换行密度（紧凑 / 舒适）', hint: 'Density', keywords: 'density hangmidu 密度 紧凑 舒适 切换', run: () => this.toggleListDensity() },
      { id: 'shortcuts', title: '键盘快捷键帮助', hint: '?', keywords: 'shortcuts kuaijiejian 快捷键 帮助', run: () => this.toggleShortcuts() },
      { id: 'export', title: '导出当前任务列表 (CSV)', hint: 'Export', keywords: 'export daochu 导出 csv 任务', run: () => this.exportToCSV() },
    ];
    // 动态：最近访问的项目
    for (const p of this.recentProjects()) {
      cmds.push({
        id: `project-${p.id}`,
        title: `打开项目：${p.name}`,
        hint: p.key || 'Project',
        keywords: `project ${p.name} ${p.key || ''} xiangmu 项目 打开 ${p.id}`,
        run: () => { void this.router.navigateByUrl(`/project/${p.id}`); },
      });
    }
    return cmds;
  }

  /** 过滤后的命令列表（computed，随 query、recentProjects、搜索结果变化） */
  readonly paletteItems = computed<PaletteCommand[]>(() => {
    const all = this.buildPaletteCommands();
    const q = this.paletteQuery().trim().toLowerCase();
    if (!q) return all;
    // 后端搜索结果（任务 + 项目 + Story + 文档 + Epic + Sprint + 通知 + Agent + Proposal）
    const results = [
      ...this.paletteTaskResults(),
      ...this.paletteProjectResults(),
      ...this.paletteStoryResults(),
      ...this.paletteDocumentResults(),
      ...this.paletteEpicResults(),
      ...this.paletteSprintResults(),
      ...this.paletteNotificationResults(),
      ...this.paletteAgentResults(),
      ...this.paletteProposalResults(),
      ...this.paletteTicketResults(),
      ...this.paletteScheduleResults(),
    ];
    const staticMatches = all.filter((c) => `${c.title} ${c.keywords || ''} ${c.hint || ''}`.toLowerCase().includes(q));
    // 命中命令时命令优先（保持 Enter 执行命令的既有行为），后端实体结果作为补充列于其后；
    // 未命中命令时直接展示后端搜索结果。
    if (staticMatches.length > 0) {
      return [...staticMatches, ...results];
    }
    return results;
  });

  paletteMove(delta: number): void {
    const n = this.paletteItems().length;
    if (n === 0) return;
    const next = (this.paletteIndex() + delta + n) % n;
    this.paletteIndex.set(next);
    this.scrollPaletteIntoView(next);
  }

  private scrollPaletteIntoView(index: number): void {
    setTimeout(() => {
      const list = document.getElementById('paletteList');
      const el = list?.querySelectorAll('.palette-item')[index] as HTMLElement | undefined;
      el?.scrollIntoView({ block: 'nearest' });
    }, 0);
  }

  paletteRun(cmd?: PaletteCommand): void {
    const target = cmd || this.paletteItems()[this.paletteIndex()];
    if (!target) return;
    this.closePalette();
    target.run();
  }

  onPaletteKeydown(event: KeyboardEvent): void {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.paletteMove(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.paletteMove(-1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      this.paletteRun();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.closePalette();
    }
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
      backlog: 0, todo: 15, in_design: 30, design_pending_review: 40,
      design_review_approved: 50, in_progress: 55, in_review: 75,
      final_review: 90, verifying: 92, done: 100, blocked: 15,
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
    this.search.set('');
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
  // v3.1 / v4.0: 筛选预设
  togglePresetOpen(): void { this.presetOpen.update((v) => !v); }
  saveFilterPreset(): void {
    const name = this.presetName().trim();
    if (!name) return;
    const preset: FilterPreset = {
      id: `preset-${Date.now()}`,
      name,
      isDefault: false,
      statuses: this.filterStatus() ? [this.filterStatus()] : [],
      priorities: [...this.filterPriorities()],
      types: [...this.filterTypes()],
      assignees: [...this.filterAssignees()],
      due: this.filterDueDate(),
      search: this.taskSearchQuery().trim(),
      mineOnly: this.filterMineOnly(),
      groupBy: this.taskGroupBy(),
      sortKey: this.taskSortKey(),
      sortOrder: this.taskSortOrder(),
    };
    this.filterPresets.set([...this.filterPresets(), preset]);
    this.persistFilterPresets();
    this.presetName.set('');
  }
  applyFilterPreset(id: string): void {
    const p = this.filterPresets().find((x) => x.id === id);
    if (!p) return;
    this.clearAllFilters();
    // 状态 / 截止日期 单选 chips
    if (p.statuses[0]) this.setQuickStatus(p.statuses[0]);
    if (p.due) this.setQuickDue(p.due);
    // 多选 chips：直接置数组并持久化
    if (p.priorities.length) { this.filterPriorities.set([...p.priorities]); this.persistQuickPriority(); }
    if (p.types.length) { this.filterTypes.set([...p.types]); this.persistQuickType(); }
    if (p.assignees.length) { this.filterAssignees.set([...p.assignees]); this.persistQuickAssignee(); }
    if (p.search) this.taskSearchQuery.set(p.search);
    if (p.mineOnly) { this.filterMineOnly.set(true); try { localStorage.setItem('agentboard_filter_mine', '1'); } catch { /* ignore */ } }
    // 分组 / 排序维度
    if (p.groupBy && p.groupBy !== 'none') {
      this.taskGroupBy.set(p.groupBy as any);
      try { localStorage.setItem('agentboard_story_group', p.groupBy); } catch { /* ignore */ }
    }
    if (p.sortKey) this.setTaskSortKey(p.sortKey);
    if (p.sortOrder) { this.taskSortOrder.set(p.sortOrder as any); try { localStorage.setItem('agentboard_sort_order', p.sortOrder); } catch { /* ignore */ } }
    this.presetOpen.set(false);
  }
  deleteFilterPreset(id: string): void {
    this.filterPresets.set(this.filterPresets().filter((p) => p.id !== id));
    this.persistFilterPresets();
  }
  // v4.0: 标记/取消默认预设（同时仅一个默认）
  setDefaultPreset(id: string): void {
    this.filterPresets.set(this.filterPresets().map((p) => ({ ...p, isDefault: p.id === id ? !p.isDefault : false })));
    this.persistFilterPresets();
  }
  // v4.0: 一键应用默认预设
  applyDefaultPreset(): void {
    const d = this.defaultPreset();
    if (d) this.applyFilterPreset(d.id);
  }
  // Epic 59 (v4.6): 应用加载时自动应用默认筛选预设（仅初始化执行一次，避免路由切换重复应用覆盖手动筛选）
  private defaultPresetApplied = false;
  applyDefaultPresetOnLoad(): void {
    if (this.defaultPresetApplied) return;
    this.defaultPresetApplied = true;
    if (this.defaultPreset()) this.applyDefaultPreset();
  }

  // Task 603: 抽屉内快速操作
  quickAdvanceStatus(): void {
    const task = this.task();
    if (!task) return;
    const order: Status[] = ['backlog', 'todo', 'in_design', 'design_pending_review', 'design_review_approved', 'in_progress', 'in_review', 'final_review', 'verifying', 'done'];
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

  // v5.2: 批量复制（克隆）选中任务到各自 Story —— 对称于单行 duplicateTask，纯前端零后端契约变更
  // v5.2: 批量复制（克隆）选中任务到各自 Story —— 对称于单行 duplicateTask，纯前端零后端契约变更
  async bulkDuplicate(): Promise<void> {
    const ids = Array.from(this.selectedTasks());
    if (ids.length === 0) return;
    this.bulkProgress.set({ current: 0, total: ids.length, message: `正在复制 0/${ids.length} 个任务…` });
    let ok = 0;
    const failed: string[] = [];
    try {
      for (let i = 0; i < ids.length; i++) {
        const id = ids[i];
        const task = this.tasks().find((t) => t.id === id);
        if (!task || !task.story_id) continue;
        try {
          await firstValueFrom(this.api.createTask(task.story_id, {
            project_id: task.project_id,
            title: task.title + ' (副本)',
            type: task.type,
            priority: task.priority,
            description: task.description,
            labels: task.labels,
          }));
          ok++;
        } catch (e) {
          failed.push(task.title);
        }
        this.bulkProgress.set({ current: i + 1, total: ids.length, message: `正在复制 ${i + 1}/${ids.length} 个任务…` });
      }
      if (failed.length) {
        this.notify(`已复制 ${ok} 个任务，${failed.length} 个失败`, 'error');
      } else {
        this.notify(`已批量复制 ${ok} 个任务（副本已创建到各自 Story）`);
      }
    } finally {
      this.bulkProgress.set(null);
      this.clearTaskSelection();
      await this.refresh();
    }
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
      const apiUrl = resolveApiBase();
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

  // Story 199: 可折叠「编辑 Story」表单
  toggleEditStory(story: { description?: string; needs_design?: boolean }): void {
    const next = !this.editStoryOpen();
    this.editStoryOpen.set(next);
    if (next) {
      this.editStoryDesc.set(story.description ?? '');
      this.editNeedsDesign.set(story.needs_design !== false);
    }
  }
  cancelEditStory(): void {
    this.editStoryOpen.set(false);
    this.editStoryDesc.set('');
  }
  async saveEditStory(story: { id: number }): Promise<void> {
    if (this.savingStory()) return;
    const desc = this.editStoryDesc();
    this.savingStory.set(true);
    try {
      await firstValueFrom(
        this.api.updateStory(story.id, { description: desc, needs_design: this.editNeedsDesign() }),
      );
      this.story.update((s) => (s ? { ...s, description: desc, needs_design: this.editNeedsDesign() } : s));
      this.editStoryOpen.set(false);
      this.notify('Story 已更新', 'success');
    } catch (e: any) {
      this.notify('更新失败：' + (e?.error?.detail ?? e?.message ?? '未知错误'), 'error');
    } finally {
      this.savingStory.set(false);
    }
  }

  quickDeleteTask(): void {
    const task = this.task();
    if (task) void this.remove('task', task.id);
  }

  /* ================= Epic 96 P0: Proposal 澄清回路 —— 问答工作台 ================= */

  proposalStatusLabel(s: ProposalStatus): string {
    return ({
      draft: '草稿',
      pending: '待开始',
      queued: '已入队',
      analyzing: '分析中',
      awaiting: '待作答',
      answered: '已作答',
      converged: '需求已明确',
      story_created: '已转 Story',
      ticket_preparing: '工单生成中',
      ticket_created: '已生成工单',
      failed: '失败',
    } as Record<string, string>)[s] || s;
  }

  /** 转换请求状态文案（文档 #59 异步生成） */
  ticketRequestStatusLabel(s: string): string {
    return ({
      pending: '排队中',
      processing: '生成中',
      done: '已生成',
      failed: '失败',
    } as Record<string, string>)[s] || s;
  }

  /** 提案失败是否属于「Agent 不可用」类——由后端 job 自动重试，前端不提供手动 retry */
  isAgentFailure(p: ProposalItem): boolean {
    const err = p.error || '';
    return ['Agent 命令无法启动', 'Agent 调用失败', 'Agent 调用异常',
            '无法启动', 'Invocation', '找不到'].some((k) => err.includes(k));
  }

  /** Tab 切换 */
  switchProposalTab(tab: 'info' | 'qa'): void {
    this.proposalTab.set(tab);
  }

  /** 打开轮次详情弹窗（点击 QA 列表项） */
  openRoundDetail(r: ProposalRoundItem): void {
    this.proposalRoundDetail.set(r);
  }

  closeRoundDetail(): void {
    this.proposalRoundDetail.set(null);
  }

  /** 单轮回答统计：X 个问题 · 已答 Y */
  roundAnsweredCount(r: ProposalRoundItem): number {
    return r.questions.filter((q) => !!q.answered_at).length;
  }

  /** 轮次整体状态：等待中 / 已完成 / 已作答 */
  roundStatusLabel(r: ProposalRoundItem): string {
    const total = r.questions.length;
    if (total === 0) return '无问题';
    const answered = this.roundAnsweredCount(r);
    if (answered === 0) return '等待中';
    if (answered < total) return `等待中 ${answered}/${total}`;
    return '已作答';
  }

  /** 当前轮次（current_round）中未答的问题——Tab 1「当前正问」展示 */
  currentOpenQuestions(): ProposalQuestionItem[] {
    const rounds = this.proposalRounds();
    const p = this.proposalItem();
    if (!p) return [];
    const r = rounds.find((x) => x.round_no === p.current_round);
    if (!r) return [];
    return r.questions.filter((q) => !q.answered_at);
  }

  /** 轮次卡片摘要（取首问题前 60 字，或 summary） */
  roundSummary(r: ProposalRoundItem): string {
    if (r.summary) return r.summary;
    if (r.questions.length) return r.questions[0].question;
    return '（无内容）';
  }

  /** 全部轮次的总问题数 */
  totalQuestionCount(): number {
    return this.proposalRounds().reduce((s, r) => s + r.questions.length, 0);
  }

  /** 全部轮次中已答问题数 */
  totalAnsweredCount(_p: ProposalItem): number {
    return this.proposalRounds().reduce(
      (s, r) => s + this.roundAnsweredCount(r), 0,
    );
  }

  /** 生成 ticket 需要的父级是否齐备（前端即时校验，后端仍兜底） */
  ticketFormValid(): boolean {
    const type = this.ticketType();
    if (type === 'epic') return true;
    if (!this.ticketEpicId()) return false;
    if (type === 'story') return true;
    return !!this.ticketStoryId();
  }

  ticketTypeLabel(t: string): string {
    return ({ epic: 'Epic', story: 'Story', task: 'Task', bug: 'Bug' } as Record<string, string>)[t] || t;
  }

  async loadProposalTicketRequests(id: number): Promise<void> {
    try {
      const rows = await firstValueFrom(this.api.listTicketRequests(id));
      this.proposalTicketRequests.set(Array.isArray(rows) ? rows : []);
    } catch {
      this.proposalTicketRequests.set([]);
    }
  }

  /** 打开详情时加载父级候选（项目 epics；选中 epic 后加载其 stories） */
  async loadTicketParents(projectId: number): Promise<void> {
    try {
      const epics = await firstValueFrom(this.api.listEpics(projectId));
      this.ticketEpics.set(Array.isArray(epics) ? epics : []);
      const eid = this.ticketEpicId();
      if (eid) {
        const stories = await firstValueFrom(this.api.listStories(eid));
        this.ticketStories.set(Array.isArray(stories) ? stories : []);
      }
    } catch {
      this.ticketEpics.set([]);
    }
  }

  onTicketTypeChange(type: string): void {
    this.ticketType.set(type as TicketType);
  }

  onTicketEpicChange(event: Event): void {
    const v = Number((event.target as HTMLSelectElement).value) || null;
    this.ticketEpicId.set(v);
    this.ticketStoryId.set(null);
    this.ticketStories.set([]);
    const eid = v;
    if (eid) {
      void firstValueFrom(this.api.listStories(eid))
        .then((rows) => this.ticketStories.set(Array.isArray(rows) ? rows : []))
        .catch(() => this.ticketStories.set([]));
    }
  }

  onTicketStoryChange(event: Event): void {
    const v = Number((event.target as HTMLSelectElement).value) || null;
    this.ticketStoryId.set(v);
  }

  /** 点击「生成 ticket」：创建转换请求 → 异步轮询状态（文档 #59） */
  async startTicketGeneration(p: ProposalItem): Promise<void> {
    const type = this.ticketType();
    if (!this.ticketFormValid()) {
      this.notify('请先选择父级（Story 需 Epic；Task/Bug 需 Epic + Story）', 'error');
      return;
    }
    const body: { type: TicketType; epic_id?: number; story_id?: number } = { type };
    if (type !== 'epic') body.epic_id = this.ticketEpicId() ?? undefined;
    if (type === 'task' || type === 'bug') body.story_id = this.ticketStoryId() ?? undefined;
    this.ticketGenerating.set(true);
    try {
      await firstValueFrom(this.api.createTicketRequest(p.id, body));
      await this.loadProposalDetail(p.id);
      await this.loadProposalTicketRequests(p.id);
      this.startTicketPolling(p.id);
      this.notify(`已提交「${this.ticketTypeLabel(type)}」生成请求，正在异步生成…`, 'success');
    } catch (e) {
      this.notify(`生成请求失败：${this.message(e)}`, 'error');
    } finally {
      this.ticketGenerating.set(false);
    }
  }

  /** ticket_preparing 期间每 3s 轮询，直到离开该状态；若用户已导航到其他视图则立即停止 */
  startTicketPolling(proposalId: number): void {
    this.stopTicketPolling();
    this._ticketPollTimer = setInterval(() => {
      // 路由守卫：当前 URL 已不在该 Proposal 详情页时停止轮询，
      // 防止用旧 proposalId 覆盖全局 proposalItem（用户打开其他提案被抢回）
      if (this.router.url.split('?')[0] !== `/proposals/${proposalId}`) {
        this.stopTicketPolling();
        return;
      }
      void this.loadProposalDetail(proposalId);
      void this.loadProposalTicketRequests(proposalId);
      const p = this.proposalItem();
      if (!p || (p.status !== 'ticket_preparing')) this.stopTicketPolling();
    }, 3000);
  }

  stopTicketPolling(): void {
    if (this._ticketPollTimer) {
      clearInterval(this._ticketPollTimer);
      this._ticketPollTimer = null;
    }
  }

  /** 列表视图：客户端二次过滤（状态由服务端过滤，关键词在本地做即时反馈） */
  proposalVisible(): ProposalItem[] {
    let list = this.proposals();
    const st = this.proposalFilterStatus();
    if (st) list = list.filter((p) => p.status === st);
    const q = this.proposalSearchQuery().trim().toLowerCase();
    if (q) {
      list = list.filter(
        (p) => p.title.toLowerCase().includes(q) || (p.content || '').toLowerCase().includes(q),
      );
    }
    return list;
  }

  async loadProposals(projectId?: number): Promise<void> {
    const params: Record<string, any> = { limit: 200 };
    if (projectId) params['project_id'] = projectId;
    if (this.proposalFilterStatus()) params['status'] = this.proposalFilterStatus();
    const rows = await firstValueFrom(this.api.listProposals(params));
    this.proposals.set(Array.isArray(rows) ? rows : []);
  }

  async onProposalFilterChange(): Promise<void> {
    try {
      await this.loadProposals(this.project()?.id);
    } catch (e) {
      this.notify(`加载提案失败：${this.message(e)}`, 'error');
    }
  }

  /** 详情工作台：拉提案主体 + 轮次问答，并用服务端已有答案初始化本地草稿 */
  async loadProposalDetail(id: number): Promise<void> {
    const [item, rounds] = await Promise.all([
      firstValueFrom(this.api.getProposal(id)),
      firstValueFrom(this.api.listProposalRounds(id)),
    ]);
    this.proposalItem.set(item);
    this.proposalRounds.set(Array.isArray(rounds) ? rounds : []);
    this.syncProposalDrafts();
    // 文档 #59：加载转换请求 + 父级候选；生成中则自动轮询
    void this.loadProposalTicketRequests(id);
    if (item?.project_id) void this.loadTicketParents(item.project_id);
    if (item?.status === 'ticket_preparing') this.startTicketPolling(id);
    // 同步 Round 详情弹窗：避免保存后弹窗仍显示旧轮次快照（看不到「已作答」）
    const detail = this.proposalRoundDetail();
    if (detail) {
      const fresh = this.proposalRounds().find((r) => r.id === detail.id);
      if (fresh) this.proposalRoundDetail.set(fresh);
    }
  }

  private syncProposalDrafts(): void {
    const drafts: Record<number, string> = {};
    const unsure: Record<number, boolean> = {};
    for (const r of this.proposalRounds()) {
      for (const q of r.questions || []) {
        drafts[q.id] = q.answer || '';
        unsure[q.id] = !!q.unsure;
      }
    }
    this.proposalDrafts.set(drafts);
    this.proposalUnsure.set(unsure);
  }

  proposalDraftOf(qid: number): string {
    return this.proposalDrafts()[qid] ?? '';
  }
  setProposalDraft(qid: number, value: string): void {
    this.proposalDrafts.update((m) => ({ ...m, [qid]: value }));
  }
  proposalUnsureOf(qid: number): boolean {
    return !!this.proposalUnsure()[qid];
  }
  toggleProposalUnsure(qid: number): void {
    this.proposalUnsure.update((m) => ({ ...m, [qid]: !m[qid] }));
  }
  isProposalQuestionSaving(qid: number): boolean {
    return this.proposalSaving().has(qid);
  }
  /** 已处理 = 有答案或被标记不确定（与后端 answered_at 判定口径一致） */
  isProposalQuestionAnswered(q: ProposalQuestionItem): boolean {
    return !!q.answered_at || !!q.unsure || !!(q.answer && q.answer.trim());
  }

  /** 当前轮（= proposal.current_round）尚未处理的问题数，用于「一键提交」按钮计数 */
  proposalPendingCount(): number {
    const cur = this.currentProposalRound();
    if (!cur) return 0;
    return (cur.questions || []).filter((q) => !this.isProposalQuestionAnswered(q)).length;
  }

  currentProposalRound(): ProposalRoundItem | null {
    const p = this.proposalItem();
    if (!p) return null;
    const rounds = this.proposalRounds();
    if (!rounds.length) return null;
    return rounds.find((r) => r.round_no === p.current_round) || rounds[rounds.length - 1];
  }

  /** 本轮是否存在「已填写但尚未提交」的草稿 */
  proposalHasDraftToSubmit(): boolean {
    const cur = this.currentProposalRound();
    if (!cur) return false;
    return (cur.questions || []).some((q) => {
      if (this.isProposalQuestionAnswered(q)) return false;
      return !!this.proposalDraftOf(q.id).trim() || this.proposalUnsureOf(q.id);
    });
  }

  /** 单条保存 */
  async saveProposalAnswer(q: ProposalQuestionItem): Promise<void> {
    if (this.isProposalQuestionSaving(q.id)) return;
    const answer = this.proposalDraftOf(q.id).trim();
    const unsure = this.proposalUnsureOf(q.id);
    if (!answer && !unsure) {
      this.notify('请填写答案，或标记为「暂不确定」', 'error');
      return;
    }
    this.proposalSaving.update((s) => new Set(s).add(q.id));
    try {
      await firstValueFrom(this.api.answerProposalQuestion(q.id, { answer, unsure }));
      await this.loadProposalDetail(q.proposal_id);
      this.notify('答案已保存', 'success');
    } catch (e) {
      this.notify(`保存失败：${this.message(e)}`, 'error');
    } finally {
      this.proposalSaving.update((s) => {
        const next = new Set(s);
        next.delete(q.id);
        return next;
      });
    }
  }

  /** 一键提交本轮：把本轮所有已填草稿串行提交，末条提交后后端自动 awaiting→answered */
  async submitProposalRound(): Promise<void> {
    if (this.proposalSubmitting()) return;
    const cur = this.currentProposalRound();
    const p = this.proposalItem();
    if (!cur || !p) return;

    const targets = (cur.questions || []).filter((q) => {
      if (this.isProposalQuestionAnswered(q)) return false;
      return !!this.proposalDraftOf(q.id).trim() || this.proposalUnsureOf(q.id);
    });
    if (!targets.length) {
      this.notify('本轮没有待提交的答案', 'error');
      return;
    }

    this.proposalSubmitting.set(true);
    let ok = 0;
    try {
      // 串行提交：后端在整轮处理完时才推进状态，串行可避免并发下的状态竞态
      for (const q of targets) {
        await firstValueFrom(
          this.api.answerProposalQuestion(q.id, {
            answer: this.proposalDraftOf(q.id).trim(),
            unsure: this.proposalUnsureOf(q.id),
          }),
        );
        ok += 1;
      }
      await this.loadProposalDetail(p.id);
      this.notify(`已提交本轮 ${ok} 条答案`, 'success');
    } catch (e) {
      // 部分成功也要刷新，避免界面与服务端不一致
      try { await this.loadProposalDetail(p.id); } catch { /* ignore */ }
      this.notify(`提交中断（已提交 ${ok}/${targets.length}）：${this.message(e)}`, 'error');
    } finally {
      this.proposalSubmitting.set(false);
    }
  }

  /* ---- 新建提案 ---- */
  async openProposalModal(): Promise<void> {
    const project = this.project();
    if (!project) {
      this.notify('请先进入一个项目，再创建提案', 'error');
      return;
    }
    this.proposalNewTitle.set('');
    this.proposalNewContent.set('');
    this.proposalNewProjectId.set(project.id);
    this.proposalModalOpen.set(true);
  }
  closeProposalModal(): void {
    this.proposalModalOpen.set(false);
  }
  async submitProposalCreate(): Promise<void> {
    const title = this.proposalNewTitle().trim();
    const pid = this.project()?.id ?? this.proposalNewProjectId();
    if (!title) {
      this.notify('请填写提案标题', 'error');
      return;
    }
    if (!pid) {
      this.notify('请选择所属项目', 'error');
      return;
    }
    try {
      const created = await firstValueFrom(
        this.api.createProposal({ project_id: pid, title, content: this.proposalNewContent() }),
      );
      this.proposalModalOpen.set(false);
      this.notify('提案已创建', 'success');
      await this.router.navigateByUrl(`/proposals/${created.id}`);
    } catch (e) {
      this.notify(`创建失败：${this.message(e)}`, 'error');
    }
  }

  /** 状态流转（草稿 → 入队派发；失败 → 重投） */
  async advanceProposalStatus(status: ProposalStatus): Promise<void> {
    const p = this.proposalItem();
    if (!p) return;
    try {
      await firstValueFrom(this.api.setProposalStatus(p.id, status));
      await this.loadProposalDetail(p.id);
      this.notify(`状态已更新为「${this.proposalStatusLabel(status)}」`, 'success');
    } catch (e) {
      this.notify(`状态更新失败：${this.message(e)}`, 'error');
    }
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
  storyTitle(sid: number | null): string {
    if (!sid) return '';
    return this.docDetailStories().find((s) => s.id === sid)?.title || this.stories().find((s) => s.id === sid)?.title || `Story #${sid}`;
  }
  projectName(pid: number): string {
    return this.projects().find((p) => p.id === pid)?.name || `#${pid}`;
  }
  docVisible(): DocumentItem[] {
    let list = this.documents();
    const q = this.docSearchQuery().trim().toLowerCase();
    if (q) return list.filter((d) => d.title.toLowerCase().includes(q) || (d.content || '').toLowerCase().includes(q));
    // 搜索词为空时按当前文件夹浏览
    const fid = this.docFolderId();
    return list.filter((d) => d.folder_id === fid);
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
    if (q) return list.filter((d) => d.title.toLowerCase().includes(q) || (d.content || '').toLowerCase().includes(q));
    // 搜索词为空时按当前文件夹浏览
    const fid = this.docFolderId();
    return list.filter((d) => d.folder_id === fid);
  }

  /* ---------- 文档文件夹：层级 / 面包屑 / 拖拽（Epic 15 增强） ---------- */
  /** 当前上下文可见的文件夹（项目 Tab 仅当前项目；全局视图全部有权限项目）。 */
  docScopeFolders(): DocumentFolder[] {
    const pid = this.project()?.id;
    if (!pid) return this.docFolders();
    return this.docFolders().filter((f) => f.project_id === pid);
  }
  /** 当前所在文件夹的直接子文件夹。 */
  docChildFolders(): DocumentFolder[] {
    const fid = this.docFolderId();
    return this.docScopeFolders().filter((f) => f.parent_id === fid);
  }
  /** 当前所在文件夹的祖先链（含自身），用于面包屑。 */
  docBreadcrumb(): Array<{ id: number | null; name: string }> {
    const chain: Array<{ id: number | null; name: string }> = [{ id: null, name: '全部文档' }];
    const byId = new Map(this.docScopeFolders().map((f) => [f.id, f]));
    let cur = this.docFolderId();
    const seen = new Set<number>();
    const path: Array<{ id: number | null; name: string }> = [];
    while (cur !== null && !seen.has(cur)) {
      seen.add(cur);
      const f = byId.get(cur);
      if (!f) break;
      path.unshift({ id: f.id, name: f.name });
      cur = f.parent_id;
    }
    return chain.concat(path);
  }
  docFolderLabel(fid: number | null): string {
    if (fid === null) return '全部文档';
    return this.docScopeFolders().find((f) => f.id === fid)?.name || `文件夹 #${fid}`;
  }
  /** 文件夹内直接文档数（含项目上下文过滤）。 */
  docFolderCount(fid: number): number {
    const pid = this.project()?.id;
    return this.documents().filter(
      (d) => d.folder_id === fid && (!pid || d.project_id === pid),
    ).length;
  }
  /** 文件夹层级深度（顶层 = 0），用于下拉选项缩进。 */
  docFolderDepth(fid: number): number {
    const byId = new Map(this.docScopeFolders().map((f) => [f.id, f]));
    let depth = 0;
    let cur: number | null = fid;
    const seen = new Set<number>();
    while (cur !== null && !seen.has(cur)) {
      seen.add(cur);
      const f = byId.get(cur);
      if (!f) break;
      depth++;
      cur = f.parent_id;
    }
    return Math.max(0, depth - 1);
  }
  /** 下拉选项标签：按层级缩进显示。 */
  docFolderOptionLabel(f: DocumentFolder): string {
    const depth = this.docFolderDepth(f.id);
    return '　'.repeat(depth) + (depth > 0 ? '└ ' : '') + f.name;
  }
  /** 进入文件夹（面包屑 / 文件夹卡片点击）。 */
  enterDocFolder(fid: number | null): void {
    this.docFolderId.set(fid);
    this.docItem.set(null);
  }
  async loadDocFolders(): Promise<void> {
    const pid = this.project()?.id;
    try {
      const folders = await firstValueFrom(
        this.api.listDocumentFolders(pid ? { project_id: pid } : undefined),
      );
      this.docFolders.set(folders || []);
    } catch {
      this.docFolders.set([]);
    }
  }
  openDocFolderModal(mode: 'create' | 'rename', folder?: DocumentFolder): void {
    if (mode === 'create') {
      const parentId = this.docFolderId(); // 默认建在当前文件夹内（创建子文件夹）
      this.docFolderName.set('');
      this.docFolderModal.set({ mode, parentId });
    } else if (folder) {
      this.docFolderName.set(folder.name);
      this.docFolderModal.set({ mode, folderId: folder.id, parentId: folder.parent_id });
    }
  }
  closeDocFolderModal(): void {
    this.docFolderModal.set(null);
  }
  async submitDocFolderModal(): Promise<void> {
    const m = this.docFolderModal();
    if (!m) return;
    const name = this.docFolderName().trim();
    if (!name) { this.notify('文件夹名称不能为空', 'error'); return; }
    try {
      if (m.mode === 'create') {
        const pid = this.project()?.id;
        if (!pid) { this.notify('请先进入项目', 'error'); return; }
        await firstValueFrom(this.api.createDocumentFolder({ project_id: pid, name, parent_id: m.parentId ?? null }));
        this.notify('文件夹已创建');
        await this.loadDocFolders();
        if (m.parentId !== null && m.parentId !== undefined) this.docFolderId.set(m.parentId);
      } else if (m.folderId !== undefined) {
        await firstValueFrom(this.api.updateDocumentFolder(m.folderId, { name }));
        this.notify('文件夹已重命名');
        await this.loadDocFolders();
      }
      this.docFolderModal.set(null);
    } catch (error) {
      this.notify(`操作失败：${this.message(error)}`, 'error');
    }
  }
  deleteDocFolder(folder: DocumentFolder): void {
    this.openConfirmation({
      title: '删除文件夹？',
      message: `确定删除文件夹「${folder.name}」？其内部文档与子文件夹会移动到上一级，不会被删除。`,
      confirmLabel: '删除文件夹',
      tone: 'danger',
    }, async () => {
      try {
        await firstValueFrom(this.api.deleteDocumentFolder(folder.id));
        this.notify('文件夹已删除');
        await this.loadDocFolders();
        await this.loadDocuments();
        if (this.docFolderId() === folder.id) this.docFolderId.set(folder.parent_id);
      } catch (error) {
        this.notify(`删除失败：${this.message(error)}`, 'error');
      }
    });
  }
  /** 拖拽开始：记录拖拽对象供 drop 目标识别。 */
  onDocDragStart(event: DragEvent, drag: { kind: 'document' | 'folder'; id: number }): void {
    // 首选通道：window 全局（手动构造的 DataTransfer 在 drop 阶段 getData 不可靠）
    (window as unknown as Record<string, unknown>)['__agentboardDrag'] = drag;
    this.docDrag.set(drag);
    const dt = event.dataTransfer;
    if (!dt) return;
    try {
      const payload = JSON.stringify(drag);
      dt.setData('application/x-agentboard-drag', payload);
      dt.setData('text/plain', payload);
      dt.effectAllowed = 'move';
    } catch {
      /* 自定义 MIME 在某些实现下受限，window 通道已兜底 */
    }
  }
  onDocDragEnd(): void {
    delete (window as unknown as Record<string, unknown>)['__agentboardDrag'];
    this.docDrag.set(null);
    this.docDropId.set(null);
  }
  /** 解析本次拖拽对象：window 全局 → dataTransfer 自定义 MIME → 组件信号。 */
  private docDragPayload(event: DragEvent): { kind: 'document' | 'folder'; id: number } | null {
    const g = (window as unknown as Record<string, unknown>)['__agentboardDrag'] as
      | { kind: 'document' | 'folder'; id: number } | undefined;
    if (g && typeof g.id === 'number') return g;
    const types = event.dataTransfer?.types;
    if (types) {
      try {
        const raw = event.dataTransfer?.getData('application/x-agentboard-drag');
        if (raw) {
          const parsed = JSON.parse(raw) as { kind: 'document' | 'folder'; id: number };
          if (parsed && typeof parsed.id === 'number') return parsed;
        }
      } catch {
        /* ignore */
      }
    }
    return this.docDrag();
  }
  onDocDropOver(event: DragEvent, target: number | 'root'): void {
    if (!this.docDragPayload(event)) return; // 仅响应本应用内部拖拽
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    this.docDropId.set(target);
  }
  onDocDropLeave(): void {
    if (this.docDropId()) this.docDropId.set(null);
  }
  async onDocDrop(event: DragEvent, target: number | 'root'): Promise<void> {
    if (this._docDropBusy) return; // drop 事件在 crumb 与面包屑容器间冒泡会触发两次
    this._docDropBusy = true;
    event.preventDefault();
    this.docDropId.set(null);
    const drag = this.docDragPayload(event) || this.docDrag();
    if (!drag) {
      this._docDropBusy = false;
      return;
    }
    const folderId = target === 'root' ? null : target;
    try {
      if (drag.kind === 'document') {
        await firstValueFrom(this.api.updateDocument(drag.id, { folder_id: folderId }));
        const patch = (x: DocumentItem) => (x.id === drag.id ? { ...x, folder_id: folderId } : x);
        this.documents.set(this.documents().map(patch));
        const current = this.docItem();
        if (current && current.id === drag.id) this.docItem.set({ ...current, folder_id: folderId });
        this.notify(folderId !== null ? `已移动到「${this.docFolderLabel(folderId)}」` : '已移动到根目录');
      } else {
        // 文件夹移动（防环由后端校验）
        await firstValueFrom(this.api.updateDocumentFolder(drag.id, { parent_id: folderId }));
        this.notify(folderId !== null ? '文件夹已移动' : '文件夹已移动到根目录');
        await this.loadDocFolders();
      }
    } catch (error) {
      this.notify(`移动失败：${this.message(error)}`, 'error');
    } finally {
      this._docDropBusy = false;
      this.docDrag.set(null);
    }
  }
  /** drop 目标是否处于高亮态。 */
  docDropActive(target: number | 'root'): boolean {
    return this.docDropId() === target && !!this.docDrag();
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
      const pid = this.project()?.id ?? null;
      if (!pid) {
        this.notify('请先进入一个项目，再创建文档', 'error');
        return;
      }
      this.docCreateProjectId.set(pid);
      this.docCreateEpicId.set(null);
      this.docCreateStoryId.set(null);
      this.docCreateTitle.set('');
      this.docCreateType.set('plan');
      this.docCreateContent.set('');
      this.docCreateFolderId.set(this.docFolderId()); // 默认建在当前浏览的文件夹内
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
          folder_id: this.docCreateFolderId(),
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
     支持：标题、粗体/斜体、行内/块代码、有序/无序列表、引用、链接、图片、分隔线、表格、以及 ```mermaid 代码块。 */
  renderMarkdown(src: string): string {
    if (!src) return '';
    const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const lines = src.replace(/\r\n/g, '\n').split('\n');
    const out: string[] = [];
    let i = 0;
    const inline = (text: string): string => {
      let t = esc(text);
      t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
      // 图片 ![alt](url)（Epic 64 S2）：协议白名单仅放行 http(s)（含 COS 预签名 URL），
      // 拒绝 javascript:/data:/vbscript: 等危险协议与属性逃逸字符（" ' < > 空白），杜绝 XSS 注入。
      t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => {
        const href = url.replace(/&amp;/g, '&');
        if (!/^https?:\/\//i.test(href) || /["'\s<>]/.test(href)) return m;
        const safeAlt = (alt || '').replace(/"/g, '&quot;');
        const safeSrc = url.replace(/"/g, '&quot;');
        return `<img src="${safeSrc}" alt="${safeAlt}" loading="lazy" referrerpolicy="no-referrer">`;
      });
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

  // 懒加载 mermaid：本地 static/mermaid.min.js → CDN fallback → 降级代码块
  // 消除 ERR_NAME_NOT_RESOLVED 控制台错误（本地文件不依赖外网）
  private _docMermaidTried = 0; // 0=local, 1=jsdelivr, 2=unpkg, 3=baomitu
  private static readonly MERMAID_SOURCES = [
    '/static/mermaid.min.js',                        // 本地（首选，无网络依赖）
    'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js',
    'https://unpkg.com/mermaid@11/dist/mermaid.min.js',
    'https://lib.baomitu.com/mermaid/11.4.0/mermaid.min.js',
  ];
  private enhanceMermaid(): void {
    const blocks = document.querySelectorAll('pre.mermaid');
    if (blocks.length === 0) return;
    if ((window as any).mermaid) { this._renderMermaid(); return; }
    if (this._docMermaidTried >= App.MERMAID_SOURCES.length) return;
    this._docMermaidLoading = true;
    const url = App.MERMAID_SOURCES[this._docMermaidTried];
    const s = document.createElement('script');
    s.src = url;
    // 本地文件无超时；CDN 源 8s 超时切换下一个
    const isLocal = url.startsWith('/');
    if (!isLocal) {
      const timer = setTimeout(() => {
        if (this._docMermaidLoading) { s.remove(); this._onMermaidLoadFail(); }
      }, 8000);
      s.addEventListener('load', () => { clearTimeout(timer); });
      s.addEventListener('error', () => { clearTimeout(timer); this._onMermaidLoadFail(); });
    } else {
      s.addEventListener('error', () => { this._onMermaidLoadFail(); });
    }
    s.addEventListener('load', () => {
      this._docMermaidLoading = false;
      try { (window as any).mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'default' }); } catch { /* ignore */ }
      this._renderMermaid();
    });
    document.head.appendChild(s);
  }
  private _onMermaidLoadFail(): void {
    this._docMermaidLoading = false;
    this._docMermaidTried++;
    if (this._docMermaidTried < App.MERMAID_SOURCES.length) {
      this.enhanceMermaid(); // 尝试下一个
    }
    // 全部失败：保留原始代码块（降级），无网络错误
  }
  private _renderMermaid(): void {
    const mermaid = (window as any).mermaid;
    if (!mermaid) return;
    document.querySelectorAll('pre.mermaid').forEach((el, idx) => {
      const code = (el.textContent || '').trim();
      if (!code) return;
      const id = `doc-mermaid-${Date.now()}-${idx}`;
      try {
        mermaid.render(id, code).then(({ svg }: any) => {
          const wrap = document.createElement('div');
          wrap.className = 'mermaid-svg';
          wrap.innerHTML = svg;
          el.replaceWith(wrap);
        }).catch((err: any) => {
          console.warn('[AgentBoard] Mermaid render error:', err?.message || err);
        });
      } catch (err) {
        console.warn('[AgentBoard] Mermaid render exception:', err);
      }
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
