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
}
