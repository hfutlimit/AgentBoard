import { TestBed } from '@angular/core/testing';

import { PaginationComponent } from './pagination';

describe('PaginationComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PaginationComponent],
    }).compileComponents();
  });

  it('stays hidden when the list fits on one page', () => {
    const fixture = TestBed.createComponent(PaginationComponent);
    fixture.componentInstance.total = 20;
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.pagination')).toBeNull();
  });

  it('shows the current range and page count', () => {
    const fixture = TestBed.createComponent(PaginationComponent);
    fixture.componentInstance.total = 131;
    fixture.componentInstance.page = 2;
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('第 21–40 条，共 131 条');
    expect(text).toContain('第 2 / 7 页');
  });

  it('emits the next page', () => {
    const fixture = TestBed.createComponent(PaginationComponent);
    fixture.componentInstance.total = 131;
    fixture.detectChanges();
    const emitted: number[] = [];
    fixture.componentInstance.pageChange.subscribe((page) => emitted.push(page));

    const nextButton = fixture.nativeElement.querySelector(
      '[aria-label="列表下一页"]',
    ) as HTMLButtonElement;
    nextButton.click();

    expect(emitted).toEqual([2]);
  });
});
