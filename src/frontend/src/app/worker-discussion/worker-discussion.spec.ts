import { TestBed } from '@angular/core/testing';
import { of, Subject, throwError } from 'rxjs';
import { vi } from 'vitest';
import { ApiService, WorkerDiscussion } from '../api.service';
import { WorkerDiscussionComponent } from './worker-discussion';

describe('Worker discussions', () => {
  const discussion: WorkerDiscussion = {
    id: 9, task_id: 42, status: 'open', subject: 'review_findings', turn: 0, max_rounds: 3,
    owner_agent: 'dev-a', reviewer_agent: 'reviewer-b', messages: [
      { comment_id: 10, reply_to_comment_id: null, agent_id: 'reviewer-b', target_agent: 'dev-a',
        decision: 'discuss', body: '<script>untrusted()</script>', evidence: ['src/app.py:12'] },
      { comment_id: 11, reply_to_comment_id: 10, agent_id: 'dev-a', target_agent: 'reviewer-b',
        decision: 'respond', position: 'disagree', body: 'Counter-evidence', evidence: ['tests/test_app.py:20'] },
    ],
  };
  const api = { workerDiscussions: vi.fn(() => of({ items: [discussion] })) };
  beforeEach(async () => {
    api.workerDiscussions.mockReset().mockReturnValue(of({ items: [discussion] }));
    await TestBed.configureTestingModule({ imports: [WorkerDiscussionComponent],
      providers: [{ provide: ApiService, useValue: api }] }).compileComponents();
  });
  it('shows participants, replies and evidence as escaped text', () => {
    const fixture = TestBed.createComponent(WorkerDiscussionComponent);
    fixture.componentRef.setInput('projectId', 8);
    fixture.componentRef.setInput('taskId', 42);
    fixture.detectChanges();
    expect(api.workerDiscussions).toHaveBeenCalledWith(8, 42, undefined);
    const page = fixture.nativeElement as HTMLElement;
    expect(page.textContent).toContain('等待 dev-a 回复');
    expect(page.textContent).toContain('回复评论 #10');
    expect(page.textContent).toContain('tests/test_app.py:20');
    expect(page.textContent).toContain('<script>untrusted()</script>');
    expect(page.querySelector('script')).toBeNull();
    expect(page.querySelectorAll('article').length).toBe(2);
  });
  it('queries Story scope and explains escalation rather than presenting success', () => {
    api.workerDiscussions.mockReturnValue(of({ items: [{ ...discussion, status: 'escalated' }] }));
    const fixture = TestBed.createComponent(WorkerDiscussionComponent);
    fixture.componentRef.setInput('projectId', 8);
    fixture.componentRef.setInput('storyId', 20);
    fixture.detectChanges();
    expect(api.workerDiscussions).toHaveBeenCalledWith(8, undefined, 20);
    expect(fixture.nativeElement.textContent).toContain('Task 已暂停');
  });
  it('cancels stale entity requests and shows service errors', () => {
    const pending = new Subject<{ items: WorkerDiscussion[] }>();
    api.workerDiscussions.mockReturnValue(pending);
    const fixture = TestBed.createComponent(WorkerDiscussionComponent);
    fixture.componentRef.setInput('projectId', 8);
    fixture.componentRef.setInput('taskId', 42);
    fixture.detectChanges();
    expect(pending.observed).toBe(true);
    api.workerDiscussions.mockReturnValue(throwError(() => new Error('not deployed')));
    fixture.componentRef.setInput('taskId', 43);
    fixture.detectChanges();
    expect(pending.observed).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('讨论暂不可用');
    expect(fixture.componentInstance.discussions()).toEqual([]);
  });
});
