import { Component, signal, computed } from '@angular/core';
import { FormsModule } from '@angular/forms';

interface CliPreset {
  label: string;
  models: string[];
  template: string;
}
interface AgentRow {
  agent_id: string;
  worker_id?: string;
  name?: string;
  roles?: string;
  capabilities?: string;
  cli_command?: string;
  model?: string;
  enabled?: boolean;
  online?: boolean;
  probe_message?: string;
  last_probe_at?: string;
  last_heartbeat?: string;
}
interface ProjectRow {
  id: number;
  name: string;
  key?: string | null;
  description?: string;
}
interface Mapping {
  project_id: number;
  name: string;
  local_dir: string;
}
interface RecordRow {
  ts: string;
  level: string;
  message: string;
}
interface ExecutionRow {
  id: number;
  schedule_id: number;
  task_id: number | null;
  project_id: number;
  project_name: string;
  project_key?: string | null;
  schedule_title: string;
  agent: string;
  agent_name?: string | null;
  model: string;
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled';
  task_title: string;
  task_description: string;
  summary?: string | null;
  error_message?: string | null;
  output_preview: string;
  has_output: boolean;
  duration_seconds: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
}
interface ExecutionDetail extends ExecutionRow {
  output?: string | null;
  log_ref?: string | null;
}

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly tab = signal<'agents' | 'mappings' | 'executions' | 'records'>('agents');

  // ---- Agent 配置 ----
  protected readonly agents = signal<AgentRow[]>([]);
  protected readonly presets = signal<Record<string, CliPreset>>({});
  protected readonly presetKeys = computed(() => Object.keys(this.presets()));
  protected readonly agentId = signal('');
  protected readonly cliType = signal('codex');
  protected readonly model = signal('');
  protected readonly enabled = signal(true);
  protected readonly agentMsg = signal('');

  // ---- 项目映射 ----
  protected readonly projects = signal<ProjectRow[]>([]);
  protected readonly mappings = signal<Record<string, Mapping>>({});
  protected readonly mapProjectId = signal<number | null>(null);
  protected readonly mapLocalDir = signal('');
  protected readonly mapMsg = signal('');

  // ---- 任务执行 ----
  protected readonly executions = signal<ExecutionRow[]>([]);
  protected readonly executionTotal = signal(0);
  protected readonly executionAgent = signal('');
  protected readonly executionStatus = signal('');
  protected readonly executionQuery = signal('');
  protected readonly executionLoading = signal(false);
  protected readonly executionMsg = signal('');
  protected readonly selectedExecution = signal<ExecutionDetail | null>(null);

  // ---- 原始日志 ----
  protected readonly records = signal<RecordRow[]>([]);
  protected readonly recordsMsg = signal('');

  protected readonly modelsFor = computed(() => {
    const p = this.presets()[this.cliType()];
    return p ? p.models : [];
  });

  constructor() {
    this.refreshAgents();
    this.refreshPresets();
    this.refreshMappings();
  }

  async api<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
    if (!r.ok) {
      const body = await r.text().catch(() => '');
      throw new Error(`${r.status}: ${body.slice(0, 200)}`);
    }
    return r.json() as Promise<T>;
  }

  selectTab(next: 'agents' | 'mappings' | 'executions' | 'records') {
    this.tab.set(next);
    if (next === 'executions' && this.executions().length === 0) this.refreshExecutions();
    if (next === 'records' && this.records().length === 0) this.refreshRecords();
  }

  // ---------- Agent ----------
  async refreshAgents() {
    try {
      this.agents.set(await this.api<AgentRow[]>('/api/agents'));
    } catch (e) {
      this.agentMsg.set(`拉取 Agent 失败：${e}`);
    }
  }

  async refreshPresets() {
    try {
      const data = await this.api<{ presets: Record<string, CliPreset> }>('/api/cli-presets');
      this.presets.set(data.presets);
      const models = data.presets[this.cliType()]?.models ?? [];
      if (models.length && !this.model()) this.model.set(models[0]);
    } catch (e) {
      this.agentMsg.set(`拉取 CLI 预设失败：${e}`);
    }
  }

  async saveAgent() {
    this.agentMsg.set('');
    if (!this.agentId().trim()) {
      this.agentMsg.set('请填写 Agent ID');
      return;
    }
    try {
      const body = {
        agent_id: this.agentId().trim(),
        cli_type: this.cliType(),
        model: this.model(),
        enabled: this.enabled(),
      };
      await this.api<unknown>('/api/agents', { method: 'POST', body: JSON.stringify(body) });
      this.agentMsg.set('✅ 已保存到当前 Worker');
      this.refreshAgents();
    } catch (e) {
      this.agentMsg.set(`保存失败：${e}`);
    }
  }

  editAgent(a: AgentRow) {
    this.agentId.set(a.agent_id);
    this.enabled.set(a.enabled !== false);
    if (a.model) this.model.set(a.model);
    // 尝试从 cli_command 识别 CLI 类型
    const cmd = a.cli_command || '';
    if (cmd.includes('codebuddy')) this.cliType.set('codebuddy');
    else if (cmd.includes('minimax')) this.cliType.set('minimax');
    this.tab.set('agents');
  }

  // ---------- 项目映射 ----------
  async refreshMappings() {
    try {
      const data = await this.api<{ projects: Record<string, Mapping> }>('/api/mappings');
      this.mappings.set(data.projects ?? {});
    } catch (e) {
      this.mapMsg.set(`拉取映射失败：${e}`);
    }
  }

  async refreshProjects() {
    try {
      const data = await this.api<{ items: ProjectRow[] }>('/api/projects');
      this.projects.set(data.items ?? []);
    } catch (e) {
      this.mapMsg.set(`拉取项目失败：${e}`);
    }
  }

  async saveMapping() {
    this.mapMsg.set('');
    if (this.mapProjectId() === null || !this.mapLocalDir().trim()) {
      this.mapMsg.set('请选择项目并填写本地目录');
      return;
    }
    const next = { ...this.mappings() };
    next[String(this.mapProjectId())] = {
      project_id: this.mapProjectId()!,
      name: this.projects().find((p) => p.id === this.mapProjectId())?.name ?? '',
      local_dir: this.mapLocalDir().trim(),
    };
    try {
      const data = await this.api<{ projects: Record<string, Mapping> }>('/api/mappings', {
        method: 'PUT',
        body: JSON.stringify({ projects: next }),
      });
      this.mappings.set(data.projects ?? {});
      this.mapMsg.set('✅ 映射已保存（本机 JSON）');
      this.mapLocalDir.set('');
      this.mapProjectId.set(null);
    } catch (e) {
      this.mapMsg.set(`保存失败：${e}`);
    }
  }

  async removeMapping(pid: number) {
    const next = { ...this.mappings() };
    delete next[String(pid)];
    try {
      const data = await this.api<{ projects: Record<string, Mapping> }>('/api/mappings', {
        method: 'PUT',
        body: JSON.stringify({ projects: next }),
      });
      this.mappings.set(data.projects ?? {});
      this.mapMsg.set('✅ 映射已删除');
    } catch (e) {
      this.mapMsg.set(`删除失败：${e}`);
    }
  }

  // ---------- 任务执行 ----------
  async refreshExecutions() {
    this.executionLoading.set(true);
    this.executionMsg.set('');
    const params = new URLSearchParams({ limit: '100' });
    if (this.executionAgent()) params.set('agent', this.executionAgent());
    if (this.executionStatus()) params.set('status', this.executionStatus());
    if (this.executionQuery().trim()) params.set('q', this.executionQuery().trim());
    try {
      const data = await this.api<{ items: ExecutionRow[]; total: number }>(
        `/api/executions?${params.toString()}`,
      );
      this.executions.set(data.items ?? []);
      this.executionTotal.set(data.total ?? 0);
      const selected = this.selectedExecution();
      if (selected && !data.items.some((item) => item.id === selected.id)) {
        this.selectedExecution.set(null);
      }
    } catch (e) {
      this.executionMsg.set(`拉取任务执行记录失败：${e}`);
    } finally {
      this.executionLoading.set(false);
    }
  }

  async toggleExecution(row: ExecutionRow) {
    if (this.selectedExecution()?.id === row.id) {
      this.selectedExecution.set(null);
      return;
    }
    this.executionMsg.set('');
    try {
      const detail = await this.api<Partial<ExecutionDetail>>(`/api/executions/${row.id}`);
      this.selectedExecution.set({ ...row, ...detail });
    } catch (e) {
      this.executionMsg.set(`拉取执行详情失败：${e}`);
    }
  }

  clearExecutionFilters() {
    this.executionAgent.set('');
    this.executionStatus.set('');
    this.executionQuery.set('');
    this.refreshExecutions();
  }

  // ---------- 原始日志 ----------
  async refreshRecords() {
    this.recordsMsg.set('');
    try {
      const data = await this.api<{ records: RecordRow[] }>('/api/records?limit=100');
      this.records.set(data.records ?? []);
    } catch (e) {
      this.recordsMsg.set(`拉取记录失败：${e}`);
    }
  }

  protected readonly mappingList = computed(() =>
    Object.values(this.mappings()).sort((a, b) => a.project_id - b.project_id),
  );

  protected readonly executionAgents = computed(() => {
    const ids = new Set(this.agents().map((agent) => agent.agent_id));
    for (const execution of this.executions()) ids.add(execution.agent);
    return [...ids].filter(Boolean).sort();
  });

  onCliChange() {
    const models = this.modelsFor();
    if (models.length) this.model.set(models[0]);
  }

  protected readonly levelClass = (lv: string) => {
    if (lv === 'ERROR' || lv === 'CRITICAL') return 'rec-error';
    if (lv === 'WARNING') return 'rec-warn';
    return 'rec-info';
  };

  protected readonly runStatusLabel = (status: string) => ({
    pending: '等待中', running: '执行中', success: '成功', failed: '失败', cancelled: '已取消',
  }[status] ?? status);

  protected readonly formatTime = (value?: string | null) => {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
  };

  protected readonly formatDuration = (seconds: number | null) => {
    if (seconds === null) return '—';
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remain = seconds % 60;
    return remain ? `${minutes}m ${remain}s` : `${minutes}m`;
  };
}
