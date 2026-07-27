import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';

const TOKEN_KEY = 'admin_portal_token';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  private token(): string | null {
    return typeof localStorage !== 'undefined' ? localStorage.getItem(TOKEN_KEY) : null;
  }

  authHeaders(): HttpHeaders {
    let h = new HttpHeaders({ 'Content-Type': 'application/json' });
    const t = this.token();
    if (t) h = h.set('Authorization', 'Bearer ' + t);
    return h;
  }

  login(username: string, password: string): Observable<any> {
    return this.http.post('/api/auth/login', { username, password });
  }

  me(): Observable<any> {
    return this.http.get('/api/auth/me', { headers: this.authHeaders() });
  }

  // ---- Admin: users ----
  listUsers(limit = 200): Observable<any> {
    return this.http.get('/api/admin/users?limit=' + limit, {
      headers: this.authHeaders(),
    });
  }

  setUserAdmin(uid: number, isAdmin: boolean): Observable<any> {
    return this.http.patch(
      '/api/admin/users/' + uid,
      { is_admin: isAdmin },
      { headers: this.authHeaders() },
    );
  }

  // ---- Admin: projects ----
  listProjects(): Observable<any> {
    return this.http.get('/api/admin/projects', {
      headers: this.authHeaders(),
    });
  }

  getProjectStats(pid: number): Observable<any> {
    return this.http.get('/api/projects/' + pid + '/stats', {
      headers: this.authHeaders(),
    });
  }
}
