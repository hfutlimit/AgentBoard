
import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, Input, OnDestroy, OnInit } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../api.service';
import { AgentRun } from '../models';

interface RunEvent {
  id: number;
  run_id: number;
  event_type: string;
  payload: unknown;
  created_at: string;
}

@Component({
  selector: 'app-agent-run-stream',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="agent-run-stream" *ngIf="runs.length > 0">
      <h3>Agent Review / Execution</h3>
      <div *ngFor="let run of runs" class="run-card">
        <div class="run-header">
          Run #{{run.id}} - <span class="status status--{{run.status}}">{{run.status}}</span>
        </div>
        <button *ngIf="eventsByRun[run.id]?.length && !noOlderEvents.has(run.id)"
          type="button" class="load-older" [disabled]="loadingOlder.has(run.id)"
          (click)="loadOlder(run.id)">加载更早事件</button>
        <div class="run-events" *ngIf="eventsByRun[run.id]">
          <div *ngFor="let evt of eventsByRun[run.id]" class="run-event">
            <span class="evt-time">{{evt.created_at | date:'HH:mm:ss'}}</span>
            <span class="evt-type">[{{evt.event_type}}]</span>
            <span class="evt-payload">{{evt.payload | json}}</span>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .agent-run-stream { margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border-color); }
    .run-card { border: 1px solid var(--border-color); border-radius: 4px; padding: 12px; margin-bottom: 12px; }
    .run-header { font-weight: bold; margin-bottom: 8px; }
    .run-events { max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; background: var(--bg-level-2); padding: 8px; }
    .run-event { margin-bottom: 4px; }
    .evt-time { color: var(--text-muted); margin-right: 8px; }
    .evt-type { color: var(--primary-color); margin-right: 8px; }
    .load-older { margin-bottom: 8px; }
  `]
})
export class AgentRunStreamComponent implements OnInit, OnDestroy {
  @Input() taskId?: number;
  runs: AgentRun[] = [];
  eventsByRun: { [runId: number]: RunEvent[] } = {};
  readonly loadingOlder = new Set<number>();
  readonly noOlderEvents = new Set<number>();
  private readonly streamControllers = new Map<number, AbortController>();

  constructor(private api: ApiService, private cdr: ChangeDetectorRef) {}

  ngOnInit(): void {
    void this.loadRuns();
  }

  private async loadRuns(): Promise<void> {
    if (!this.taskId) return;
    try {
      const result = await firstValueFrom(this.api.listTaskRuns(this.taskId));
      this.runs = result.items || [];
      for (const run of this.runs) {
        if (run.status === 'running' || run.status === 'pending') {
          void this.streamRun(run.id);
        }
      }
      this.cdr.detectChanges();
    } catch {
      this.runs = [];
    }
  }

  private async streamRun(runId: number): Promise<void> {
    const controller = new AbortController();
    this.streamControllers.set(runId, controller);
    try {
      await this.api.streamRunEvents(runId, event => {
        const runEvent = event as unknown as RunEvent;
        if (!this.eventsByRun[runId]) this.eventsByRun[runId] = [];
        if (!this.eventsByRun[runId].some(existing => existing.id === runEvent.id)) {
          this.eventsByRun[runId].push(runEvent);
          if (this.eventsByRun[runId].length > 500) this.eventsByRun[runId].splice(0, this.eventsByRun[runId].length - 500);
        }
        const status = this.terminalStatus(runEvent);
        if (status) {
          const index = this.runs.findIndex(run => run.id === runId);
          if (index >= 0) this.runs[index] = { ...this.runs[index], status };
          controller.abort();
          this.streamControllers.delete(runId);
        }
        this.cdr.detectChanges();
      }, controller.signal);
    } catch {
      if (!controller.signal.aborted) this.cdr.detectChanges();
    } finally {
      this.streamControllers.delete(runId);
    }
  }

  async loadOlder(runId: number): Promise<void> {
    if (this.loadingOlder.has(runId) || this.noOlderEvents.has(runId)) return;
    const current = this.eventsByRun[runId] || [];
    const beforeId = current[0]?.id;
    if (!beforeId) return;
    this.loadingOlder.add(runId);
    try {
      const older = await firstValueFrom(this.api.listRunEvents(runId, beforeId));
      if (!older.length) {
        this.noOlderEvents.add(runId);
      } else {
        const byId = new Map<number, RunEvent>();
        for (const event of [...older.reverse(), ...current]) {
          const typed = event as unknown as RunEvent;
          byId.set(typed.id, typed);
        }
        this.eventsByRun[runId] = [...byId.values()].slice(0, 500);
      }
      this.cdr.detectChanges();
    } catch {
      this.cdr.detectChanges();
    } finally {
      this.loadingOlder.delete(runId);
    }
  }

  private terminalStatus(event: RunEvent): AgentRun['status'] | null {
    const payload = event.payload as { status?: string } | null;
    const status = payload?.status || event.event_type.replace(/^run\./, '');
    return status === 'success' || status === 'failed' || status === 'cancelled' ? status : null;
  }

  ngOnDestroy(): void {
    this.streamControllers.forEach(controller => controller.abort());
    this.streamControllers.clear();
  }
}

import { ViewEncapsulation, inject } from '@angular/core';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import { WorkerDiscussionComponent } from '../worker-discussion/worker-discussion';
import { ProjectDataService } from '../services/project-data.service';

/** Task detail rendered as an entity tab inside the project workspace. */
@Component({
  selector: 'app-task-detail-view',
  standalone: true,
  imports: [CommonModule, WorkspaceHeadingComponent, AgentRunStreamComponent, WorkerDiscussionComponent],
  templateUrl: './task-detail-view.html',
  encapsulation: ViewEncapsulation.None,
})
export class TaskDetailViewComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  openEntity(event: MouseEvent, kind: 'epic' | 'story' | 'task', id: number, title?: string): void {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    void this.host.openWorkspaceEntity(kind, id, title);
  }
}
