import { Component, signal, computed } from '@angular/core';
import { FormsModule } from '@angular/forms';

interface CliPreset {
  label: string;
  models: string[];
  template: string;
}
interface AgentRow {
  agent_id: string;
  name: string;
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

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly tab = signal<'agents' | 'mappings' | 'records'>('agents');

  // ---- Agent 配置 ----
  protected readonly agents = signal<AgentRow[]>([]);
  protected readonly presets = signal<Record<string, CliPreset>>({});
  protected readonly presetKeys = computed(() => Object.keys(this.presets()));
  protected readonly agentId = signal('');
  protected readonly agentName = signal('');
  protected readonly cliType = signal('codebuddy');
  protected readonly model = signal('');
  protected readonly enabled = signal(true);
  protected readonly agentMsg = signal('');

  // ---- 项目映射 ----
  protected readonly projects = signal<ProjectRow[]>([]);
  protected readonly mappings = signal<Record<string, Mapping>>({});
  protected readonly mapProjectId = signal<number | null>(null);
  protected readonly mapLocalDir = signal('');
  protected readonly mapMsg = signal('');

  // ---- 处理记录 ----
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
    if (!this.agentId().trim() || !this.agentName().trim()) {
      this.agentMsg.set('请填写 Agent ID 与名称');
      return;
    }
    try {
      const body = {
        agent_id: this.agentId().trim(),
        name: this.agentName().trim(),
        cli_type: this.cliType(),
        model: this.model(),
        enabled: this.enabled(),
      };
      await this.api<unknown>('/api/agents', { method: 'POST', body: JSON.stringify(body) });
      this.agentMsg.set('✅ 已保存（服务器 agents 表已更新）');
      this.refreshAgents();
    } catch (e) {
      this.agentMsg.set(`保存失败：${e}`);
    }
  }

  editAgent(a: AgentRow) {
    this.agentId.set(a.agent_id);
    this.agentName.set(a.name);
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

  // ---------- 处理记录 ----------
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

  onCliChange() {
    const models = this.modelsFor();
    if (models.length) this.model.set(models[0]);
  }

  protected readonly levelClass = (lv: string) => {
    if (lv === 'ERROR' || lv === 'CRITICAL') return 'rec-error';
    if (lv === 'WARNING') return 'rec-warn';
    return 'rec-info';
  };
}
