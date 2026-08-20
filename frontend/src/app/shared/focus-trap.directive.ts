import {
  AfterViewInit,
  Directive,
  ElementRef,
  EventEmitter,
  HostListener,
  inject,
  OnDestroy,
  Output,
  Renderer2,
} from '@angular/core';

/**
 * FocusTrapDirective — Epic 151 / Story 328 a11y。
 *
 * 标准 ARIA modal/dialog 焦点陷阱（focus trap）行为：
 * 1. **初始聚焦**：宿主元素挂载后，焦点自动跳到第一个 focusable 子元素
 *    （或宿主本身如果它本身可聚焦）。如果上次聚焦位置在容器内，恢复之。
 * 2. **Tab 循环**：按 Tab 在容器内的最后一个 focusable 后跳回第一个；
 *    Shift+Tab 反向同理。
 * 3. **Esc 关闭**：默认不做事（避免误关），但 emit `appFocusTrapEscape` 事件
 *    让宿主自行决定（关 modal / popover）。如果宿主没监听，按 Esc 也能让
 *    焦点自然脱出（很多 modal 在背景监听 keydown.escape 关闭）。
 * 4. **焦点恢复**：宿主元素销毁时，把焦点恢复到挂载前的 activeElement。
 *
 * 用法：
 *
 *   <div class="modal-overlay" appFocusTrap (appFocusTrapEscape)="closeModal()">
 *     <section class="modal" role="dialog" aria-modal="true" aria-labelledby="...">
 *       ...
 *     </section>
 *   </div>
 *
 * 限制：
 * - 不会阻止背景（backdrop）的鼠标点击（modal-overlay 通常自己处理）；
 * - 不阻止 Tab 跳到浏览器 chrome（地址栏等），只保证 Tab 在容器内循环；
 * - 不接管 Arrow 键 / Home / End（那些属于 grid / listbox 行为）。
 */
@Directive({
  selector: '[appFocusTrap]',
  standalone: true,
  host: {
    '[attr.tabindex]': '"-1"',
  },
  outputs: ['appFocusTrapEscape'],
})
export class FocusTrapDirective implements AfterViewInit, OnDestroy {
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly renderer = inject(Renderer2);

  /** Esc 键事件，宿主 modal 可监听以关闭。 */
  @Output() readonly appFocusTrapEscape = new EventEmitter<void>();

  /** Tab / Shift+Tab 事件，焦点在容器内循环。 */
  private previouslyFocused: HTMLElement | null = null;
  private keydownHandler: ((e: KeyboardEvent) => void) | null = null;

  ngAfterViewInit(): void {
    const el = this.host.nativeElement;
    this.previouslyFocused = (document.activeElement as HTMLElement) || null;

    // 初始聚焦（延后一帧让宿主内容先渲染）
    queueMicrotask(() => {
      const first = this.firstFocusable(el);
      if (first instanceof HTMLElement) {
        first.focus();
      } else {
        // 容器无 focusable 子元素 → 聚焦宿主本身（tabindex=-1）
        el.focus();
      }
    });

    // 监听 keydown 拦截 Tab / Shift+Tab 实现循环
    this.keydownHandler = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const focusables = this.allFocusable(el);
      if (focusables.length === 0) {
        // 容器无 focusable → 阻止 Tab 跳出
        e.preventDefault();
        el.focus();
        return;
      }
      const active = document.activeElement as HTMLElement | null;
      const idx = active ? focusables.indexOf(active) : -1;
      if (e.shiftKey) {
        // Shift+Tab：第一个 → 跳到最后一个
        if (idx <= 0) {
          e.preventDefault();
          focusables[focusables.length - 1].focus();
        }
      } else {
        // Tab：最后一个 → 跳回第一个
        if (idx === focusables.length - 1) {
          e.preventDefault();
          focusables[0].focus();
        }
      }
    };
    el.addEventListener('keydown', this.keydownHandler);
  }

  ngOnDestroy(): void {
    const el = this.host.nativeElement;
    if (this.keydownHandler) el.removeEventListener('keydown', this.keydownHandler);
    // 焦点恢复
    if (this.previouslyFocused && document.contains(this.previouslyFocused)) {
      try {
        this.previouslyFocused.focus();
      } catch {
        /* focus target detached; ignore */
      }
    }
  }

  /**
   * Esc 键也直接 emit escape 事件（方便没在 host 监听 keydown.escape 的
   * 简单 modal 使用 directive 关闭）。注意我们不 preventDefault——
   * 让浏览器默认 Esc 行为也照常。
   */
  @HostListener('keydown.escape', ['$event'])
  onEscape(_e: Event): void {
    this.appFocusTrapEscape.emit();
  }

  // --- helpers ---

  private allFocusable(root: HTMLElement): HTMLElement[] {
    const selector = [
      'a[href]',
      'button:not([disabled])',
      'input:not([disabled]):not([type="hidden"])',
      'select:not([disabled])',
      'textarea:not([disabled])',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',');
    const nodes = Array.from(root.querySelectorAll<HTMLElement>(selector));
    return nodes.filter((n) => n.offsetParent !== null || n === document.activeElement);
  }

  private firstFocusable(root: HTMLElement): HTMLElement | null {
    return this.allFocusable(root)[0] ?? null;
  }
}
