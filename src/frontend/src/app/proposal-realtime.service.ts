import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
import { resolveSignalRBase } from './api.service';

export interface ProposalQuestionRaised {
  proposal_id: number;
  project_id: number;
  round: number;
  workflow: string;
  event: string;
}

@Injectable({ providedIn: 'root' })
export class ProposalRealtimeService {
  private readonly questionRaisedSubject = new Subject<ProposalQuestionRaised>();
  readonly questionRaised$ = this.questionRaisedSubject.asObservable();
  private connection?: import('@microsoft/signalr').HubConnection;
  private starting?: Promise<void>;
  private stopRequested = false;

  start(): void {
    if (this.connection || this.starting) return;
    this.stopRequested = false;
    this.starting = this.connect()
      .catch(() => undefined)
      .finally(() => { this.starting = undefined; });
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
    this.connection = connection;
    try {
      await connection.start();
      if (this.stopRequested) await this.stop();
    } catch (error) {
      if (this.connection === connection) this.connection = undefined;
      await connection.stop().catch(() => undefined);
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.stopRequested = true;
    const connection = this.connection;
    this.connection = undefined;
    if (connection && connection.state !== 'Disconnected') await connection.stop();
  }
}
