export type ItemType = 'task' | 'bug';
export type Status = 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'verifying' | 'done';
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
  task_id: number;
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
