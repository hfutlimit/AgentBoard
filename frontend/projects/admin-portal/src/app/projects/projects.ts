import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../api.service';
import { NavBar } from '../nav-bar/nav-bar';

@Component({
  selector: 'app-projects',
  imports: [CommonModule, NavBar],
  templateUrl: './projects.html',
  styleUrl: './projects.css',
})
export class Projects implements OnInit {
  projects = signal<any[]>([]);
  loading = signal(true);
  error = signal('');
  private api = inject(ApiService);

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading.set(true);
    this.error.set('');
    this.api.listProjects().subscribe({
      next: (res: any) => {
        this.projects.set(Array.isArray(res) ? res : res.items || []);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('加载项目列表失败');
        this.loading.set(false);
      },
    });
  }

  fmtDate(s: string): string {
    if (!s) return '—';
    const d = new Date(s);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  }
}
