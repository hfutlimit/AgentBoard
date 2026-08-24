import { Component, inject, OnInit, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { forkJoin } from 'rxjs';
import { ApiService } from '../api.service';
import { NavBar } from '../nav-bar/nav-bar';

type Gran = 'day' | 'week' | 'month';

interface Bucket {
  key: string;
  label: string;
  created: number;
  done: number;
}

@Component({
  selector: 'app-stats',
  imports: [CommonModule, NavBar],
  templateUrl: './stats.html',
  styleUrl: './stats.css',
})
export class Stats implements OnInit {
  projects = signal<any[]>([]);
  selected = signal<number | 'all'>('all');
  granularity = signal<Gran>('day');
  loading = signal(true);
  error = signal('');

  private dailyCreated = signal<Map<string, number>>(new Map());
  private dailyDone = signal<Map<string, number>>(new Map());
  private summaryRaw = signal<{ total: number; done: number; active: number; backlog: number }>({
    total: 0,
    done: 0,
    active: 0,
    backlog: 0,
  });

  summary = computed(() => {
    const s = this.summaryRaw();
    return {
      ...s,
      completion_rate: s.total ? +(s.done / s.total * 100).toFixed(1) : 0,
    };
  });

  chartData = computed<Bucket[]>(() => {
    const g = this.granularity();
    const created = this.dailyCreated();
    const done = this.dailyDone();
    const days = new Set<string>([...created.keys(), ...done.keys()]);
    const buckets = new Map<string, Bucket>();
    for (const day of days) {
      const c = created.get(day) || 0;
      const dn = done.get(day) || 0;
      const bk = this.bucketKey(day, g);
      const b = buckets.get(bk.key) || { key: bk.key, label: bk.label, created: 0, done: 0 };
      b.created += c;
      b.done += dn;
      buckets.set(bk.key, b);
    }
    return [...buckets.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  });

  maxVal = computed(() => {
    let m = 0;
    for (const b of this.chartData()) m = Math.max(m, b.created, b.done);
    return m || 1;
  });

  private api = inject(ApiService);

  ngOnInit() {
    this.api.listProjects().subscribe({
      next: (res: any) => {
        const list = Array.isArray(res) ? res : res?.items || [];
        this.projects.set(list);
        this.loadStats();
      },
      error: () => {
        this.error.set('加载项目列表失败');
        this.loading.set(false);
      },
    });
  }

  loadStats() {
    this.loading.set(true);
    this.error.set('');
    const sel = this.selected();
    const targets = sel === 'all' ? this.projects().map((p) => p.id) : [sel];
    if (targets.length === 0) {
      this.loading.set(false);
      return;
    }
    forkJoin(targets.map((id) => this.api.getProjectStats(id))).subscribe({
      next: (statsList: any[]) => {
        const createdMap = new Map<string, number>();
        const doneMap = new Map<string, number>();
        const totals = { total: 0, done: 0, active: 0, backlog: 0 };
        for (const st of statsList) {
          for (const d of st.daily_created || [])
            createdMap.set(d.day, (createdMap.get(d.day) || 0) + d.count);
          for (const d of st.daily_done || [])
            doneMap.set(d.day, (doneMap.get(d.day) || 0) + d.count);
          totals.total += st.total_tasks || 0;
          totals.done += st.done_tasks || 0;
          totals.active += st.active_tasks || 0;
          totals.backlog += st.backlog_tasks || 0;
        }
        this.summaryRaw.set(totals);
        this.dailyCreated.set(createdMap);
        this.dailyDone.set(doneMap);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('加载统计数据失败');
        this.loading.set(false);
      },
    });
  }

  onProjectChange(e: Event) {
    const v = (e.target as HTMLSelectElement).value;
    this.selected.set(v === 'all' ? 'all' : Number(v));
    this.loadStats();
  }

  setGran(g: Gran) {
    this.granularity.set(g);
  }

  barHeight(v: number): number {
    return Math.round((v / this.maxVal()) * 100);
  }

  private bucketKey(day: string, g: Gran): { key: string; label: string } {
    const dt = new Date(day + 'T00:00:00');
    if (g === 'day') {
      return { key: day, label: day.slice(5) };
    }
    if (g === 'month') {
      const key = day.slice(0, 7);
      return { key, label: key };
    }
    // week: Monday of the ISO week (local date components, avoid UTC shift)
    const d = new Date(dt);
    const dow = d.getDay(); // 0 Sun .. 6 Sat
    const diff = dow === 0 ? 6 : dow - 1;
    d.setDate(d.getDate() - diff);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const key = `${y}-${m}-${dd}`;
    const label = `${d.getMonth() + 1}/${d.getDate()}`;
    return { key, label };
  }
}
