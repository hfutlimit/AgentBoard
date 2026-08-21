import { Component, ViewEncapsulation, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * DetailPaneComponent — 2026-08-21 (Epic152 v3) master-detail side panel
 *
 * 工作台内的 side panel,展示用户从 *-tab 内部点开的详情链接
 * (Story / Task / Epic / Proposal / Sprint / Document)。
 *
 * 设计原则:
 * - 不与顶层 /story/:id / /task/:id / /epic/:id / /proposals/:id 全页路由冲突
 * - 用户从 *-tab 内部点 link → side panel 出现,workspace tab 上下文不丢
 * - 顶部仍能全局点进全页详情(命令面板 / 通知 / 直接 URL bar 输 / 顶栏 switcher)
 *
 * Step 1 实现：占位 panel (kind + id + 关闭 + open full page 链接)。
 * Step 2 (下一个 commit) 会把 app.html @case ('story' / 'task' / 'epic' / 'proposal')
 *   提取到独立 component,side panel 用真实详情渲染。
 */
export type DetailKind = 'story' | 'task' | 'epic' | 'proposal' | 'sprint' | 'document';

export interface DetailSelection {
  kind: DetailKind;
  id: number;
  /** 可选：详情加载后的 fallback 标题（e.g. story.title）。 */
  title?: string;
}

const KIND_META: Record<DetailKind, { label: string; iconId: string; fullPath: (id: number) => string }> = {
  story:    { label: 'Story', iconId: 'i-message', fullPath: (id) => `/task/${id}` },
  task:     { label: 'Task',  iconId: 'i-list',    fullPath: (id) => `/task/${id}` },
  epic:     { label: 'Epic',  iconId: 'i-flag',    fullPath: (id) => `/epic/${id}` },
  proposal: { label: '提案',   iconId: 'i-message', fullPath: (id) => `/proposals/${id}` },
  sprint:   { label: 'Sprint', iconId: 'i-flag',    fullPath: (id) => `/sprint/${id}` },
  document: { label: '文档',   iconId: 'i-file',    fullPath: (id) => `/documents/${id}` },
};

@Component({
  selector: 'app-detail-pane',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './detail-pane.html',
  styleUrl: './detail-pane.css',
  encapsulation: ViewEncapsulation.None,
})
export class DetailPaneComponent {
  /** 当前选中的 detail;null 时 panel 不渲染 */
  readonly selection = input<DetailSelection | null>(null);

  /** 用户点 × 关闭 */
  readonly close = output<void>();

  /** 用户点 "open in full page" 链接(顶层全页路由) */
  readonly openFull = output<DetailSelection>();

  readonly meta = computed(() => {
    const sel = this.selection();
    return sel ? KIND_META[sel.kind] : null;
  });

  readonly fullPath = computed(() => {
    const sel = this.selection();
    const m = this.meta();
    if (!sel || !m) return null;
    return m.fullPath(sel.id);
  });

  onClose(event: Event): void {
    event.stopPropagation();
    this.close.emit();
  }

  onOpenFull(event: MouseEvent): void {
    // 让 <a routerLink> 自然 navigate,不再 trigger 这里;但我们 emit 一下让 shell 关闭 panel
    this.openFull.emit(this.selection()!);
    void event;
  }
}
