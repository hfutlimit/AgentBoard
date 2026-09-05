import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, OnDestroy, inject, signal } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, WorkerDiscussion } from '../api.service';

/** Task-local discussion, aggregated by Story. Text binding deliberately escapes Agent output. */
@Component({
  selector: 'app-worker-discussion', standalone: true, imports: [CommonModule],
  template: `
    <section class="discussion-card" aria-label="Agent 协作讨论">
      <header><h3>Agent 协作讨论</h3><button type="button" (click)="refresh()" [disabled]="loading()">刷新</button></header>
      @if (error()) { <p role="status">{{ error() }}</p> }
      @for (d of discussions(); track d.id) {
        <details open>
          <summary>讨论 #{{ d.id }} · Task #{{ d.task_id }} · {{ label(d.status) }} · {{ label(d.subject) }}</summary>
          <p class="participants">{{ d.reviewer_agent }} ↔ {{ d.owner_agent }} · 最多 {{ d.max_rounds }} 轮</p>
          @if (d.status === 'open') {
            <p>等待 {{ d.turn % 2 ? d.reviewer_agent : d.owner_agent }} 回复。讨论结束前不返工、不生成 Bug。</p>
          }
          @if (d.status === 'escalated') { <p role="status">未达成一致，Task 已暂停，等待人工裁决。结论应记录在 Task / Story 评论中。</p> }
          @for (m of d.messages; track m.comment_id) {
            <article>
              <strong>{{ m.agent_id }} → {{ m.target_agent || '讨论结论' }}</strong>
              <span> · {{ label(m.decision) }} {{ m.position ? '· ' + label(m.position) : '' }} · 评论 #{{ m.comment_id }}</span>
              @if (m.reply_to_comment_id) { <small>回复评论 #{{ m.reply_to_comment_id }}</small> }
              <p class="message">{{ m.body }}</p>
              @if (m.evidence.length) { <ul>@for (ref of m.evidence; track $index) { <li>{{ ref }}</li> }</ul> }
            </article>
          }
        </details>
      } @empty { @if (!loading() && !error()) { <p>暂无讨论。Agent 提出问题后，会在这里展示双方回复与结论。</p> } }
    </section>`,
  styles: [`:host{display:block;margin:16px 0}.discussion-card{border:1px solid var(--border,#dfe5ec);border-radius:10px;padding:18px}
    header{display:flex;justify-content:space-between;align-items:center}h3{margin:0}button{cursor:pointer}
    details{margin-top:16px}summary{cursor:pointer;font-weight:600}.participants,small{opacity:.7}small{display:block}
    article{border-left:3px solid #429285;padding:10px 14px;margin:12px 0;background:rgba(70,130,120,.05)}
    .message,li{white-space:pre-wrap;overflow-wrap:anywhere}p{line-height:1.6}`],
})
export class WorkerDiscussionComponent implements OnChanges, OnDestroy {
  @Input() projectId?: number;
  @Input() taskId?: number;
  @Input() storyId?: number;
  private readonly api = inject(ApiService);
  private request?: Subscription;
  readonly discussions = signal<WorkerDiscussion[]>([]);
  readonly loading = signal(false);
  readonly error = signal('');
  ngOnChanges() { this.refresh(); }
  ngOnDestroy() { this.request?.unsubscribe(); }
  refresh() {
    this.request?.unsubscribe();
    this.discussions.set([]); this.error.set(''); this.loading.set(false);
    if (!this.projectId || (!this.taskId && !this.storyId)) return;
    this.loading.set(true);
    this.request = this.api.workerDiscussions(this.projectId, this.taskId, this.storyId).subscribe({
      next: result => { this.discussions.set(result.items); this.loading.set(false); },
      error: () => { this.error.set('讨论暂不可用，请确认服务端已启用新版 worker-owned 功能。'); this.loading.set(false); },
    });
  }
  label(value: string) {
    const labels: Record<string, string> = { open: '讨论中', confirmed: '已确认', withdrawn: '已撤回', escalated: '待人工裁决',
      review_findings: '评审疑问', qa_defects: 'QA 缺陷核实', discuss: '提出疑问', respond: '回复', confirm: '确认问题',
      withdraw: '撤回疑问', escalate: '请求人工', agree: '同意', disagree: '不同意', clarify: '需要澄清' };
    return labels[value] || value;
  }
}
