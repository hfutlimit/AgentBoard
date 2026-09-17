import {
  ChangeDetectionStrategy,
  Component,
  Renderer2,
  computed,
  effect,
  inject,
  input,
  output,
} from '@angular/core';

/**
 * ManualRefreshButtonComponent — 统一工作区手动刷新按钮（Story #437 / openspec:unified-manual-refresh-controls）
 *
 * 收敛 proposal list / proposal detail / story tasks / 根 Task 工具栏四处手动刷新入口。
 * 形状固定 36×36 方形图标按钮，图标/spinner 均为 16px；不渲染任何可视"刷新/刷新中"文字。
 * 父视图继续拥有 `refreshing` signal 与刷新方法；本组件只负责呈现和事件转发。
 *
 * 使用：
 * <app-manual-refresh-button
 *   [buttonId]="'proposal-list-refresh-btn'"
 *   [label]="'刷新提案列表'"
 *   [refreshing]="refreshing"
 *   (refresh)="refresh.emit()"
 * />
 */
@Component({
  selector: 'app-manual-refresh-button',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './manual-refresh-button.html',
  styleUrl: './manual-refresh-button.css',
})
export class ManualRefreshButtonComponent {
  /** 渲染到内部原生 `<button>` 的稳定 id；保留现有测试和自动化选择器。 */
  readonly buttonId = input<string>('');
  /** 非空、面向用户的目标名称；用作静止态 `title` 与 `aria-label`。 */
  readonly label = input<string>('刷新当前视图');
  /** `true` 时用 spinner 替换图标、禁用原生按钮、暴露忙碌状态。 */
  readonly refreshing = input(false);

  readonly refresh = output<void>();

  readonly resolvedTitle = computed(() =>
    this.refreshing() ? `正在${this.label()}` : this.label(),
  );

  private readonly renderer = inject(Renderer2);

  constructor() {
    effect(() => {
      // 把 cssClass 输入映射到 host element；本组件当前不需要 cssClass 输入,
      // 保留 hook 方便未来追加宿主样式。
    });
  }

  onClick(event: MouseEvent): void {
    if (this.refreshing()) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    this.refresh.emit();
  }
}