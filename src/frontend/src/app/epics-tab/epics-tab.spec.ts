import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { EpicsTabComponent } from './epics-tab';

describe('EpicsTabComponent', () => {
  let fixture: ComponentFixture<EpicsTabComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [EpicsTabComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(EpicsTabComponent);
    fixture.componentRef.setInput('epics', []);
    fixture.componentRef.setInput('statuses', ['todo', 'in_progress', 'in_review', 'done', 'blocked']);
    fixture.detectChanges();
  });

  it('renders the all-status default and the five supported business statuses', () => {
    const select = fixture.nativeElement.querySelector('#project-epic-status-filter') as HTMLSelectElement;

    expect(select).toBeTruthy();
    expect(select.value).toBe('');
    expect(fixture.nativeElement.querySelector('.empty-state-guide')).toBeTruthy();
    expect([...select.options].map((option) => option.value)).toEqual([
      '',
      'todo',
      'in_progress',
      'in_review',
      'done',
      'blocked',
    ]);
  });

  it('emits the selected status when the toolbar selection changes', () => {
    const selected = vi.fn();
    fixture.componentInstance.filterStatusChange.subscribe(selected);
    const select = fixture.nativeElement.querySelector('#project-epic-status-filter') as HTMLSelectElement;

    select.value = 'in_review';
    select.dispatchEvent(new Event('change'));

    expect(selected).toHaveBeenCalledWith('in_review');
  });
});
