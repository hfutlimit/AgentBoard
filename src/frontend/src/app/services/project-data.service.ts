import { Injectable } from '@angular/core';

/**
 * Transitional dependency-injection port for lazy project routes.
 *
 * It carries no state. Routed views delegate to the root App's existing
 * signals/actions until those domains move into focused stores.
 */
@Injectable({ providedIn: 'root' })
export class ProjectDataService {
  private workspaceHost: unknown = null;

  bindWorkspaceHost(host: unknown): void {
    this.workspaceHost = host;
  }

  getWorkspaceHost<T>(): T {
    if (!this.workspaceHost) {
      throw new Error('Project workspace host is not bound');
    }
    return this.workspaceHost as T;
  }
}
