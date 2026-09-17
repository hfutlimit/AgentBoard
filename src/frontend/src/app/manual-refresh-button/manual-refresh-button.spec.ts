import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { ManualRefreshButtonComponent } from './manual-refresh-button';

@Component({
  standalone: true,
  imports: [ManualRefreshButtonComponent],
  template: `<app-manual-refresh-button
    [buttonId]="buttonId()"
    [label]="label()"
    [refreshing]="refreshing()"
    (refresh)="onRefresh()"
  />`,
})
class HostFixtureComponent {
  readonly buttonId = signal('test-refresh-btn');
  readonly label = signal('刷新当前视图');
  readonly refreshing = signal(false);
  refreshCount = 0;
  onRefresh(): void {
    this.refreshCount += 1;
  }
}

describe('ManualRefreshButtonComponent (Story #437)', () => {
  let fixture: ComponentFixture<HostFixtureComponent>;
  let host: HostFixtureComponent;
  let button: HTMLButtonElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HostFixtureComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(HostFixtureComponent);
    host = fixture.componentInstance;
    // OnPush + signal-based child; let Angular auto-react to signal changes
    // so we don't trip NG0100 ExpressionChangedAfterItHasBeenCheckedError.
    fixture.autoDetectChanges();
    button = fixture.debugElement.query(By.css('.manual-refresh-button')).nativeElement as HTMLButtonElement;
  });

  it('renders a 36x36 square icon button with the stable id', () => {
    expect(button.classList.contains('manual-refresh-button')).toBe(true);
    expect(button.classList.contains('btn')).toBe(true);
    expect(button.classList.contains('refresh-btn')).toBe(true);
    expect(button.id).toBe('test-refresh-btn');
    expect(button.getAttribute('aria-busy')).toBeNull();
    expect(button.querySelector('.refresh-icon')).not.toBeNull();
    expect(button.querySelector('.refresh-icon')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('uses the label for both tooltip and aria-label when idle', () => {
    host.label.set('刷新提案列表');
    fixture.detectChanges();
    expect(button.getAttribute('title')).toBe('刷新提案列表');
    expect(button.getAttribute('aria-label')).toBe('刷新提案列表');
  });

  it('overrides the title with 正在 + label while refreshing', () => {
    host.label.set('刷新提案列表');
    host.refreshing.set(true);
    fixture.detectChanges();
    expect(button.getAttribute('title')).toBe('正在刷新提案列表');
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.querySelector('.refresh-spinner')).not.toBeNull();
    expect(button.querySelector('.refresh-icon')).toBeNull();
  });

  it('disables the button while refreshing', () => {
    host.refreshing.set(true);
    fixture.detectChanges();
    expect(button.disabled).toBe(true);
    expect(button.classList.contains('is-busy')).toBe(true);
  });

  it('emits refresh on click when not refreshing', () => {
    button.click();
    expect(host.refreshCount).toBe(1);
  });

  it('does not emit refresh while refreshing', () => {
    host.refreshing.set(true);
    fixture.detectChanges();
    button.click();
    expect(host.refreshCount).toBe(0);
  });

  it('never renders a visible 刷新/刷新中 text label', () => {
    // 静止 + 忙碌两个状态都不应该出现任何可视文字标签
    expect(button.textContent?.trim()).toBe('');
    host.refreshing.set(true);
    fixture.detectChanges();
    expect(button.textContent?.trim()).toBe('');
    host.refreshing.set(false);
    fixture.detectChanges();
    expect(button.textContent?.trim()).toBe('');
  });

  it('returns to idle after a busy cycle', () => {
    host.refreshing.set(true);
    fixture.detectChanges();
    expect(button.disabled).toBe(true);

    host.refreshing.set(false);
    fixture.detectChanges();
    expect(button.disabled).toBe(false);
    expect(button.querySelector('.refresh-icon')).not.toBeNull();
    expect(button.querySelector('.refresh-spinner')).toBeNull();
    button.click();
    expect(host.refreshCount).toBe(1);
  });
});