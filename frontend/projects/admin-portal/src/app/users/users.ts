import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../api.service';
import { NavBar } from '../nav-bar/nav-bar';

@Component({
  selector: 'app-users',
  imports: [CommonModule, NavBar],
  templateUrl: './users.html',
  styleUrl: './users.css',
})
export class Users implements OnInit {
  users = signal<any[]>([]);
  loading = signal(true);
  error = signal('');
  busyId = signal<number | null>(null);
  private api = inject(ApiService);

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading.set(true);
    this.error.set('');
    this.api.listUsers().subscribe({
      next: (res: any) => {
        this.users.set(res.items || []);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('加载用户列表失败');
        this.loading.set(false);
      },
    });
  }

  toggleAdmin(u: any) {
    if (this.busyId() === u.id) return;
    this.busyId.set(u.id);
    this.api.setUserAdmin(u.id, !u.is_admin).subscribe({
      next: (updated: any) => {
        this.users.update((list) =>
          list.map((x) => (x.id === updated.id ? { ...x, is_admin: updated.is_admin } : x)),
        );
        this.busyId.set(null);
      },
      error: () => {
        this.error.set('更新管理员权限失败');
        this.busyId.set(null);
      },
    });
  }

  fmtDate(s: string): string {
    if (!s) return '—';
    const d = new Date(s);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}
