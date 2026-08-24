// Story 265：状态收敛为 5 值（todo/in_progress/in_review/done/blocked）
export type ItemType = 'dev' | 'bug' | 'qa' | 'design';
// Story 自身保留 confirmed（用户确认闸门）但 Task 不再使用 confirmed/backlog
// 注：Epic/Story 后端支持 backlog，前端类型须对齐（Epic 145 B-A6 验证时发现脱节）
export type Status = 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked' | 'backlog';
export type Priority = 'highest' | 'high' | 'medium' | 'low' | 'lowest';
/** 项目工作区内 tab 种类（阶段3 Story 319：提取 OverviewTabComponent 时从 app.ts 提升为共享类型） */
export type ProjectTabKind = 'overview' | 'epics' | 'backlog' | 'proposals' | 'settings' | 'members' | 'stats' | 'schedules' | 'documents' | 'kanban' | 'sprints' | 'tickets';

export interface Project {
  id: number;
  name: string;
  key: string | null;
  description: string;
  is_private: boolean;
  created_at: string;
  membership_role?: 'owner' | 'member';
  // Story 137（项目中心）：归档机制
  is_archived?: boolean;
  archived_at?: string | null;
  archived_by?: number | null;
  // 项目中心专用统计字段（仅 /api/projects/center 响应包含）
  task_count?: number;
  task_done?: number;
  member_count?: number;
  last_activity_at?: string | null;
}

export interface UserProfile {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  avatar_url: string | null;
  is_admin: boolean;
  created_at: string;
}

export interface ApiKeyInfo {
  id: number;
  name: string;
  prefix: string;
  permissions: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface Epic {
  id: number;
  project_id: number;
  title: string;
  description: string;
  status: Status;
  created_at: string;
}

export interface Story {
  id: number;
  epic_id: number;
  title: string;
  description: string;
  status: Status;
  needs_design: boolean;
  in_kanban?: boolean;
  created_at: string;
}

/* ---------- Kanban (Epic 130: 项目看板) ---------- */
export interface KanbanTaskMini {
  id: number;
  type: string;
  title: string;
  status: string;
  priority: string;
  assignee_id: number | null;
  estimate?: number | null;
}
export interface KanbanStory {
  id: number;
  epic_id: number;
  title: string;
  description: string;
  status: string;
  needs_design: boolean;
  in_kanban: boolean;
  tasks: KanbanTaskMini[];
  created_at: string;
}
export interface KanbanBoard {
  columns: Record<string, KanbanStory[]>;
  items: KanbanStory[];
}

export interface Task {
  id: number;
  project_id: number;
  story_id: number | null;
  sprint_id: number | null;
  type: ItemType;
  title: string;
  status: Status;
  priority: Priority;
  description: string;
  spec: string;
  source_spec_id: number | null;
  due_date: string | null;  // ISO date string YYYY-MM-DD
  assignee_id: number | null;
  labels: string;  // JSON array string
  estimate: number | null;  // 预估工时（小时）
  created_at: string;
  updated_at: string;
}

export interface Comment {
  id: number;
  task_id: number | null;
  story_id: number | null;
  epic_id: number | null;
  author: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface AuthResult {
  id: number;
  username: string;
  is_admin: boolean;
  token: string;
}

export type SprintStatus = 'planning' | 'active' | 'completed';

export interface Sprint {
  id: number;
  project_id: number;
  title: string;
  goal: string;
  status: SprintStatus;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectMember {
  id: number;
  project_id: number;
  user_id: number;
  role: 'owner' | 'member';
  joined_at: string;
  username: string | null;
}

// Ticket 全流程（2026-08-09）：Agent 注册表（前端 Agent 池视图 + 配置中心）
export interface AgentRow {
  id: number;
  agent_id: string;
  name: string;
  roles: string;          // JSON array string
  capabilities: string;   // JSON array string
  cli_command: string;    // 支持 {model} 占位符（同 CLI 多 agent 各注入模型）
  model: string;
  auth_key: string;
  user_id: number | null;
  online: boolean;
  enabled: boolean;
  last_heartbeat: string | null;
  probe_message: string;  // Worker 定期 probe 结果详情
  last_probe_at: string | null;
  created_at: string;
  updated_at: string;
}

// Story 状态变更历史（Ticket 全流程）
export interface StoryStatusHistoryRow {
  id: number;
  story_id: number;
  from_status: string;
  to_status: string;
  changed_by: number | null;
  reason: string;
  created_at: string;
}

export interface Notification {
  id: number;
  user_id: number;
  type: 'project_invite' | 'join_request' | 'task_assigned' | 'status_changed' | 'mentioned';
  title: string;
  content: string;
  is_read: boolean;
  link: string | null;
  created_at: string;
}

export interface Attachment {
  id: number;
  task_id: number;
  filename: string;
  original_name: string;
  size: number;
  mime_type: string;
  created_at: string;
}

export interface AgentSchedule {
  id: number;
  project_id: number;
  title: string;
  schedule_type: 'once' | 'cron';
  cron_expr: string | null;
  // Story 106：绑定松绑（agent / 固定任务 / 可选筛选）
  agent: string | null;
  task_id: number | null;
  task_priority: string | null;
  task_type: string | null;
  epic_id: number | null;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentRun {
  id: number;
  schedule_id: number;
  task_id: number | null;
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled';
  idempotency_key: string | null;
  started_at: string | null;
  finished_at: string | null;
  output: string | null;
  error_message: string | null;
  summary: string | null;
  created_at: string;
  // Epic 135 v6.20: 全局搜索（/api/search/runs）附加归属项目 id（join AgentSchedule 取得）
  project_id?: number;
}

export interface ProjectStats {
  daily_created: Array<{ day: string; count: number }>;
  daily_done: Array<{ day: string; count: number }>;
  active_tasks: number;
  backlog_tasks: number;
  total_tasks: number;
  done_tasks: number;
  completion_rate: number;
}

// Epic 122 S3 M2 / S4: 项目级评审统计运营视图
export interface ReviewBucketStats {
  total: number;
  approved: number;
  rejected: number;
  pending: number;
  blocked: number;
}

export interface ReviewReviewerAgg {
  user_id: number;
  name: string | null;
  story_reviewed: number;
  task_reviewed: number;
  story_approved: number;
  story_rejected: number;
  task_approved: number;
  task_rejected: number;
}

// 多数决评审投票进度（S4 M2，majority 模式下填充；single 模式 votes 为空数组）
export interface ReviewVoteRow {
  kind: 'story' | 'task';
  id: number;
  title: string;
  status: string;
  approve: number;
  reject: number;
  cast: number;
  quorum: number;
}

export interface ReviewStats {
  project_id: number;
  days: number;
  stories: ReviewBucketStats;
  tasks: ReviewBucketStats;
  rounds: { avg_story_round: number; avg_task_round: number };
  reject_rate: number;
  timeout_pending: number;
  by_reviewer: ReviewReviewerAgg[];
  review_mode?: 'single' | 'majority';
  review_quorum?: number;
  votes?: ReviewVoteRow[];
  generated_at: string;
}

// POST /api/review-stats/reassign-timeout 结果（scan_review_timeouts 返回）
export interface ReviewTimeoutResult {
  stories_reassigned?: number;
  tasks_reassigned?: number;
  blocked?: number;
  no_candidate?: number;
  stories_settled?: number;
  tasks_settled?: number;
}

// Epic 117 (Task 995): 首页 Dashboard 单请求聚合统计（跨项目）
export interface OverviewStats {
  counts: {
    projects: number;
    epics: number;
    stories: number;
    tasks: number;
    done_tasks: number;
  };
  projects: Array<{ id: number; name: string; total: number; done: number; percent: number }>;
  status_distribution: Array<{ status: string; count: number }>;
  activity_7d: Array<{ day: string; count: number }>;
}

export interface PagedResult<T> {
  items: T[];
  total: number;
}

export interface ApiErrorBody {
  detail?: string | Array<{ msg?: string }>;
}

export interface TaskDependency {
  id: number;
  task_id: number;
  type: 'blocks' | 'blocked_by' | 'relates_to';
  task: Task | null;
}

export interface TaskDependencies {
  blockers: TaskDependency[];
  blocked_by: TaskDependency[];
}

export interface AuditLog {
  id: number;
  user_id: number | null;
  action: string;
  entity_type: string;
  entity_id: number | null;
  method: string;
  path: string;
  ip_address: string | null;
  user_agent: string | null;
  response_status: number | null;
  duration_ms: number | null;
  created_at: string;
}

export interface WebhookConfig {
  id: number;
  name: string;
  url: string;
  enabled: boolean;
  events: string[];
  created_at: string;
}

/* ---------- Epic 15: 项目文档维护（多成员 / 多 Agent 协作） ---------- */
export type DocumentType = 'memory' | 'plan' | 'knowledge' | 'design';
export type DocumentStatus = 'draft' | 'in_review' | 'approved' | 'cancelled';

export const DOCUMENT_TYPES: DocumentType[] = ['memory', 'plan', 'knowledge', 'design'];
export const DOCUMENT_STATUSES: DocumentStatus[] = ['draft', 'in_review', 'approved', 'cancelled'];

export interface DocumentItem {
  id: number;
  project_id: number;
  epic_id: number | null;
  story_id: number | null;
  folder_id: number | null;
  title: string;
  content: string;
  type: DocumentType;
  status: DocumentStatus;
  author_id: number | null;
  author: string | null;
  created_at: string;
  updated_at: string;
  // Epic 139：当前 revision 头指针（无 revision 历史时为 null）
  current_revision_id: number | null;
  current_revision_number: number | null;
}

/** 文档文件夹（Epic 15 增强）：parent_id 自引用形成任意层级子文件夹，null = 顶层。 */
export interface DocumentFolder {
  id: number;
  project_id: number;
  parent_id: number | null;
  name: string;
  created_at: string;
  updated_at: string;
}

/** Epic 139：不可变 revision 快照。 */
export interface DocumentRevisionItem {
  id: number;
  document_id: number;
  revision_number: number;
  title: string;
  content: string;
  author_id: number | null;
  author: string | null;
  change_note: string;
  is_restore: boolean;
  restored_from_revision: number | null;
  created_at: string;
}

export interface DocumentCommentItem {
  id: number;
  document_id: number;
  author: string;
  content: string;
  created_at: string;
  updated_at: string;
}

/* ---------- Epic 96 P0: Proposal 澄清回路（人机协同需求分析） ---------- */
// 2026-08-08 文档 #59：新增 pending（待开始）/ ticket_preparing（工单生成中）/
// ticket_created（已生成工单，泛化 story_created）
export type ProposalStatus =
  | 'draft' | 'pending' | 'queued' | 'analyzing' | 'awaiting'
  | 'answered' | 'converged' | 'story_created'
  | 'ticket_preparing' | 'ticket_created' | 'failed';

export const PROPOSAL_STATUSES: ProposalStatus[] = [
  'draft', 'pending', 'queued', 'analyzing', 'awaiting',
  'answered', 'converged', 'story_created',
  'ticket_preparing', 'ticket_created', 'failed',
];

export interface ProposalItem {
  id: number;
  project_id: number;
  title: string;
  content: string;
  status: ProposalStatus;
  current_round: number;
  converged_spec: string;
  story_id: number | null;
  ticket_type: string;
  ticket_id: number | null;
  author_id: number | null;
  error: string;
  created_at: string;
  updated_at: string;
}

/* ---- Proposal → Ticket 异步转化（文档 #59）---- */
export type TicketType = 'epic' | 'story' | 'task' | 'bug';
export type TicketRequestStatus = 'pending' | 'processing' | 'done' | 'failed';

export interface TicketRequestItem {
  id: number;
  proposal_id: number;
  project_id?: number;  // v6.18 搜索端点附加（经提案反查，命令面板显示项目名）
  type: string;
  parent_epic_id:  number | null;
  parent_story_id: number | null;
  title: string;
  status: TicketRequestStatus;
  ticket_id: number | null;
  error: string;
  created_at: string;
  updated_at: string;
}

/** 统一工单（Epic/Story/Task 聚合），用于「工单」视图 */
export interface TicketItem {
  type: 'epic' | 'story' | 'task';
  id: number;
  title: string;
  status: string;
  priority: string | null;
  created_at: string | null;
  updated_at: string | null;
  assignee_id: number | null;
  assignee_name: string | null;
  epic_id: number | null;
  story_id: number | null;
}

export interface ProposalQuestionItem {
  id: number;
  proposal_id: number;
  round_id: number;
  seq: number;
  question: string;
  answer: string;
  unsure: boolean;
  answered_at: string | null;
  answered_by: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProposalRoundItem {
  id: number;
  proposal_id: number;
  round_no: number;
  summary: string;
  agent: string;
  created_at: string;
  updated_at: string;
  questions: ProposalQuestionItem[];
}
