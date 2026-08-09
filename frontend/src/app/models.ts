export type ItemType = 'task' | 'bug' | 'test_execution' | 'design';
// 2026-08-09 Ticket 全流程：Story 新增 confirmed（用户确认闸门），Task 用不到但同属 Status 联合
export type Status = 'backlog' | 'confirmed' | 'todo' | 'in_design' | 'design_pending_review' | 'design_review_approved' | 'in_progress' | 'in_review' | 'final_review' | 'verifying' | 'done' | 'blocked';
export type Priority = 'highest' | 'high' | 'medium' | 'low' | 'lowest';

export interface Project {
  id: number;
  name: string;
  key: string | null;
  description: string;
  is_private: boolean;
  created_at: string;
  membership_role?: 'owner' | 'member';
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
  created_at: string;
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
  status: 'pending' | 'running' | 'success' | 'failed';
  idempotency_key: string | null;
  started_at: string | null;
  finished_at: string | null;
  output: string | null;
  error_message: string | null;
  created_at: string;
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
  type: string;
  parent_epic_id: number | null;
  parent_story_id: number | null;
  title: string;
  status: TicketRequestStatus;
  ticket_id: number | null;
  error: string;
  created_at: string;
  updated_at: string;
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
