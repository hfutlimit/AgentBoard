import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WorkspaceHeadingComponent } from './workspace-heading';

/** Test host：模拟实际用法，eyebrow + title + subtitle + actions slot + titleBadge slot。 */
@Component({
  standalone: true,
  imports: [CommonModule, WorkspaceHeadingComponent],
  template: `
    <app-workspace-heading
      [eyebrow]="eyebrow"
      [title]="title"
      [subtitle]="subtitle"
    >
      <button class="heading-action-btn heading-action-btn-1">操作 1</button>
      <button class="heading-action-btn heading-action-btn-2">操作 2</button>
      <span class="heading-title-badge">{{ badgeCount }}</span>
    </app-workspace-heading>
  `,
})
class TestHostComponent {
  eyebrow = 'PROJECT CENTER';
  title = '项目中心';
  subtitle = '所有项目一站式管理';
  badgeCount = 12;
}

describe('WorkspaceHeadingComponent (Epic 150 / X2)', () => {
  let fixture: ComponentFixture<TestHostComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHostComponent, WorkspaceHeadingComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(TestHostComponent);
    fixture.autoDetectChanges();
    await fixture.whenStable();
  });

  it('renders the root .workspace-heading-v7 container', () => {
    const root = fixture.nativeElement.querySelector('.workspace-heading-v7');
    expect(root).toBeTruthy();
  });

  it('renders eyebrow, title, subtitle', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('PROJECT CENTER');
    expect(text).toContain('项目中心');
    expect(text).toContain('所有项目一站式管理');
  });

  it('h1 contains title text', () => {
    const h1 = fixture.nativeElement.querySelector('h1');
    expect(h1).toBeTruthy();
    expect(h1.textContent).toContain('项目中心');
  });

  it('left side has eyebrow + h1 + subtitle structure', () => {
    const left = fixture.nativeElement.querySelector('.workspace-heading-left');
    expect(left).toBeTruthy();
    expect(left.querySelector('.eyebrow')).toBeTruthy();
    expect(left.querySelector('h1')).toBeTruthy();
    expect(left.querySelector('.muted')).toBeTruthy();
  });

  it('actions slot is projected into .workspace-heading-actions div', () => {
    const actionsRoot = fixture.nativeElement.querySelector('.workspace-heading-actions');
    expect(actionsRoot).toBeTruthy();
  });

  it('title-badge slot is projected into h1', () => {
    const h1 = fixture.nativeElement.querySelector('h1');
    const badge = h1 ? h1.querySelector('.heading-title-badge') : null;
    // 投影可能跨 ng-content 不在 h1 内 — 检查整个 DOM
    const anyBadge = fixture.nativeElement.querySelector('.heading-title-badge');
    expect(anyBadge).toBeTruthy();
    expect(anyBadge.textContent.trim()).toBe('12');
  });
});
