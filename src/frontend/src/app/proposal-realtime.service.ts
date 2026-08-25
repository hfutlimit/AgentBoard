import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
import { resolveApiBase, resolveSignalRBase } from './api.service';

export interface ProposalQuestionRaised {
  proposal_id: number;
  project_id: number;
  round: number;
  workflow: string;
  event: string;
}

/**
 * Connects the goal-mode UI to the .NET BFF SignalR hub.
 *
 * P1-5 + P1-9: the hub now exposes `JoinProject` / `LeaveProject` so the
 * server only forwards `ProposalQuestionRaised` to the `project:{id}`
 * group whose members the caller has verified project membership for.
 * Browsers start with no subscriptions, and we lazy-join whenever the
 * app navigates into a project view. We also bail out early when no
 * auth token is present so anonymous SPA sessions do not open a
 * no-op SignalR handshake.
 */
@Injectable({ providedIn: 'root' })
export class ProposalRealtimeService {
  private readonly questionRaisedSubject = new Subject<ProposalQuestionRaised>();
  readonly questionRaised$ = this.questionRaisedSubject.asObservable();
  private connection?: import('@microsoft/signalr').HubConnection;
  private starting?: Promise<void>;
  private stopRequested = false;
  private readonly joinedProjects = new Set<number>();

  start(): void {
    if (this.connection || this.starting) return;
    // P1-9: no token means the hub's [Authorize] handshake will 401
    // immediately. Skip the round-trip and any UI churn that comes with
    // reconnect attempts while the user is on the login screen.
    if (!this.authToken()) return;
    this.stopRequested = false;
    this.starting = this.connect()
      .catch(() => undefined)
      .finally(() => { this.starting = undefined; });
  }

  /**
   * Subscribe to proposal question events for a single project. No-op
   * until the connection is established; rejects if the server denies
   * project membership (the hub raises an error which propagates here).
   */
  async joinProject(projectId: number): Promise<void> {
    if (this.joinedProjects.has(projectId)) return;
    if (!this.connection) {
      // Defer: the app will retry once `start()` resolves.
      this.joinedProjects.add(projectId);
      void this.starting?.then(() => this.joinProject(projectId));
      return;
    }
    try {
      await this.connection.invoke('JoinProject', projectId);
      this.joinedProjects.add(projectId);
    } catch (error) {
      // Drop the speculative join so the next attempt re-tries cleanly.
      this.joinedProjects.delete(projectId);
      throw error;
    }
  }

  async leaveProject(projectId: number): Promise<void> {
    if (!this.joinedProjects.delete(projectId)) return;
    if (!this.connection) return;
    try {
      await this.connection.invoke('LeaveProject', projectId);
    } catch {
      // Best-effort; the connection is going away on logout anyway.
    }
  }

  private authToken(): string | null {
    if (typeof localStorage === 'undefined') return null;
    return localStorage.getItem('agentboard_token');
  }

  private async connect(): Promise<void> {
    const { HubConnectionBuilder } = await import('@microsoft/signalr');
    const connection = new HubConnectionBuilder()
      .withUrl(`${resolveSignalRBase()}/hubs/proposals`, {
        accessTokenFactory: () => localStorage.getItem('agentboard_token') || '',
      })
      .withAutomaticReconnect([0, 1000, 3000, 10000])
      .build();
    connection.on('ProposalQuestionRaised', (payload: ProposalQuestionRaised) => {
      this.questionRaisedSubject.next(payload);
    });
    // On reconnect the server has dropped every group, so re-join the
    // projects we previously subscribed to.
    connection.onreconnected(() => {
      for (const id of this.joinedProjects) {
        void connection.invoke('JoinProject', id).catch(() => undefined);
      }
    });
    this.connection = connection;
    try {
      await connection.start();
      if (this.stopRequested) await this.stop();
      // Flush any join calls that were queued before the connection came up.
      for (const id of this.joinedProjects) {
        await connection.invoke('JoinProject', id).catch(() => undefined);
      }
    } catch (error) {
      if (this.connection === connection) this.connection = undefined;
      await connection.stop().catch(() => undefined);
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.stopRequested = true;
    this.joinedProjects.clear();
    const connection = this.connection;
    this.connection = undefined;
    if (connection && connection.state !== 'Disconnected') await connection.stop();
  }
}
