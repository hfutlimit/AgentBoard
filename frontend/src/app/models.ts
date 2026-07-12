export type ItemType = 'task' | 'bug';
export type Status = 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'verifying' | 'done';
export type Priority = 'highest' | 'high' | 'medium' | 'low' | 'lowest';

export interface Project {
  id: number;
  name: string;
  key: string | null;
  description: string;
  created_at: string;
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

export interface ApiErrorBody {
  detail?: string | Array<{ msg?: string }>;
}
