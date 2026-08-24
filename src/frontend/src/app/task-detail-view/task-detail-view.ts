
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
  `]
})
export class AgentRunStreamComponent implements OnInit, OnDestroy {
  @Input() taskId?: number;
  runs: AgentRun[] = [];
  eventsByRun: { [runId: number]: RunEvent[] } = {};
  private readonly streamControllers: AbortController[] = [];

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
    this.streamControllers.push(controller);
    try {
      await this.api.streamRunEvents(runId, event => {
        const runEvent = event as unknown as RunEvent;
        if (!this.eventsByRun[runId]) this.eventsByRun[runId] = [];
        this.eventsByRun[runId].push(runEvent);
        this.cdr.detectChanges();
      }, controller.signal);
    } catch {
      if (!controller.signal.aborted) this.cdr.detectChanges();
    }
  }

  ngOnDestroy(): void {
    this.streamControllers.forEach(controller => controller.abort());
  }
}

import { ViewEncapsulation, inject } from '@angular/core';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import { ProjectDataService } from '../services/project-data.service';

/** Task detail rendered as an entity tab inside the project workspace. */
@Component({
  selector: 'app-task-detail-view',
  standalone: true,
  imports: [CommonModule, WorkspaceHeadingComponent, AgentRunStreamComponent],
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
