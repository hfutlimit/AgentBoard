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
 * P1-5 / P1-9 / P2: subscription state is split into two sets so a
 * mid-reconnect ``joinProject`` call does not poison the desired state.
 *  - ``desiredProjects`` — what the app currently wants to be subscribed to.
 *  - ``joinedProjects`` — what the server has actually confirmed.
 * During a re-connect window, ``joinProject`` keeps the id in
 * ``desiredProjects`` even if the hub invoke throws; ``onreconnected``
 * replays the desired set against the (newly re-established) connection
 * and only then promotes the id into ``joinedProjects``. This avoids
 * the previous race where a failed invoke would delete the project
 * from the only tracking set and the reconnect handler would never
 * re-subscribe.
 */
@Injectable({ providedIn: 'root' })
export class ProposalRealtimeService {
  private readonly questionRaisedSubject = new Subject<ProposalQuestionRaised>();
  readonly questionRaised$ = this.questionRaisedSubject.asObservable();
  private connection?: import('@microsoft/signalr').HubConnection;
  private starting?: Promise<void>;
  private stopRequested = false;
  private readonly desiredProjects = new Set<number>();
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
   * Mark a project as "wanted" and try to join the corresponding
   * SignalR group. The project is added to ``desiredProjects``
   * immediately so a later ``onreconnected`` will always pick it up,
   * even if the live ``invoke`` call fails (e.g. the connection is
   * mid-reconnect).
   */
  async joinProject(projectId: number): Promise<void> {
    if (this.desiredProjects.has(projectId)) {
      // Already wanted. If we are joined too there is nothing to do.
      if (this.joinedProjects.has(projectId)) return;
    } else {
      this.desiredProjects.add(projectId);
    }
    if (!this.connection) {
      // Defer: the app will retry once `start()` resolves via the
      // flush loop inside connect() / onreconnected().
      void this.starting?.then(() => this.joinProject(projectId));
      return;
    }
    if (this.connection.state !== 'Connected') {
      // The connection is alive (we have a reference) but not currently
      // Connected — leave the id in desiredProjects and wait for
      // onreconnected to drive the actual join.
      return;
    }
    try {
      await this.connection.invoke('JoinProject', projectId);
      this.joinedProjects.add(projectId);
    } catch {
      // Leave desiredProjects intact. onreconnected will retry.
    }
  }

  async leaveProject(projectId: number): Promise<void> {
    if (!this.desiredProjects.delete(projectId)) return;
    this.joinedProjects.delete(projectId);
    if (!this.connection || this.connection.state !== 'Connected') return;
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
    // P2: on (re)connect, the server has dropped every group; replay
    // the desired set and only then promote the ids into joinedProjects.
    // The handler also fires after the very first ``connection.start()``,
    // so it doubles as the flush for any joinProject calls that were
    // queued while the connection was being established.
    const flushDesired = async (): Promise<void> => {
      // Clear joinedProjects: nothing is currently joined on the new
      // server-side connection. desiredProjects is the source of truth.
      this.joinedProjects.clear();
      for (const id of this.desiredProjects) {
        try {
          await connection.invoke('JoinProject', id);
          this.joinedProjects.add(id);
        } catch {
          // Network blip; the next onreconnected tick will retry.
        }
      }
    };
    connection.onreconnected(() => {
      void flushDesired();
    });
    this.connection = connection;
    try {
      await connection.start();
      if (this.stopRequested) await this.stop();
      // First connect also needs the flush so projects the app called
      // joinProject for before the connection was up end up subscribed.
      await flushDesired();
    } catch (error) {
      if (this.connection === connection) this.connection = undefined;
      await connection.stop().catch(() => undefined);
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.stopRequested = true;
    this.desiredProjects.clear();
    this.joinedProjects.clear();
    const connection = this.connection;
    this.connection = undefined;
    if (connection && connection.state !== 'Disconnected') await connection.stop();
  }
}
