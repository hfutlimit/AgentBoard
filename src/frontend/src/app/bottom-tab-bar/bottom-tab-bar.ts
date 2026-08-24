import { Component, computed, inject, signal, ViewEncapsulation } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd } from '@angular/router';
import { filter, map, startWith } from 'rxjs/operators';

/**
 * BottomTabBarComponent — Epic 151 / Story 328 移动端导航（< 840px 显示）。
 *
 * 背景：Story 327 删了旧 emoji tab-bar 后，< 840px 视口下项目工作台
 * 无可见入口。本组件提供 iOS/Android 风格的底部 tab bar，与 navy
 * project-sidebar-v7 互斥（CSS media query 互斥显示）。
 *
 * 设计：
 * - 5 个核心入口：首页 / 项目 / 当前项目（如有）/ 通知 / 我的
 * - "当前项目" 槽位：view() === 'project' 时显示当前 tab label + 图标
 *   点击弹底部 sheet 切 8 tab（不实现全 sheet，简化：仅跳转到当前 project 的 overview）
 * - 大于 840px 视口：CSS 隐藏，不影响桌面布局
 * - ARIA：每个 button 都有 aria-label
 *
 * 视口判断不依赖 JS（CSS 媒体查询），符合渐进增强。
 */
@Component({
  selector: 'app-bottom-tab-bar',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <nav class="bottom-tab-bar" aria-label="主导航（移动端）">
      <a [routerLink]="['/']"
         class="btb-item"
         [class.btb-active]="activeView() === 'home'"
         aria-label="首页"
         [attr.aria-current]="activeView() === 'home' ? 'page' : null">
        <svg class="btb-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        <span class="btb-label">首页</span>
      </a>
      <a [routerLink]="['/projects']"
         class="btb-item"
         [class.btb-active]="activeView() === 'projects'"
         aria-label="项目"
         [attr.aria-current]="activeView() === 'projects' ? 'page' : null">
        <svg class="btb-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
        <span class="btb-label">项目</span>
      </a>
      <a [routerLink]="currentProjectId() ? ['/project', currentProjectId(), 'overview'] : ['/projects']"
         class="btb-item"
         [class.btb-active]="activeView() === 'project'"
         [attr.aria-label]="currentProjectId() ? '当前项目工作台' : '工作台'"
         [attr.aria-current]="activeView() === 'project' ? 'page' : null">
        <svg class="btb-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
        <span class="btb-label">工作台</span>
      </a>
      <a [routerLink]="['/notifications']"
         class="btb-item"
         [class.btb-active]="activeView() === 'notifications'"
         aria-label="通知"
         [attr.aria-current]="activeView() === 'notifications' ? 'page' : null">
        <svg class="btb-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
        <span class="btb-label">通知</span>
      </a>
      <a [routerLink]="['/settings']"
         class="btb-item"
         [class.btb-active]="activeView() === 'settings'"
         aria-label="我的"
         [attr.aria-current]="activeView() === 'settings' ? 'page' : null">
        <svg class="btb-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span class="btb-label">我的</span>
      </a>
    </nav>
  `,
  styles: [`
    /* Story 328: 移动端 < 840px 显示；桌面隐藏 */
    .bottom-tab-bar {
      display: none;
    }
    @media (max-width: 840px) {
      .bottom-tab-bar {
        display: flex;
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 50;
        background: var(--color-surface, #0c1322);
        border-top: 1px solid var(--color-border, #1f2937);
        padding: 6px 8px calc(6px + env(safe-area-inset-bottom, 0px));
        gap: 4px;
        align-items: stretch;
        justify-content: space-around;
        box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.25);
      }
      .btb-item {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        flex: 1;
        min-height: 48px; /* a11y 触控最小高度 */
        padding: 4px 6px;
        text-decoration: none;
        color: var(--color-muted, #94a3b8);
        border-radius: 8px;
        gap: 2px;
        transition: color 0.15s ease, background 0.15s ease;
      }
      .btb-item:hover, .btb-item:focus-visible {
        color: var(--color-text, #e2e8f0);
        background: rgba(255, 255, 255, 0.05);
        outline: none;
      }
      .btb-item.btb-active {
        color: var(--brand-primary, #38bdf8);
        background: rgba(56, 189, 248, 0.10);
      }
      .btb-icon {
        width: 22px;
        height: 22px;
        flex-shrink: 0;
      }
      .btb-label {
        font-size: 11px;
        line-height: 1.2;
        font-weight: 500;
      }
      /* 给主内容区底部留出空间避免被 bottom bar 遮挡 */
      .layout {
        padding-bottom: 64px;
      }
    }
  `],
  encapsulation: ViewEncapsulation.None,
})
export class BottomTabBarComponent {
  private readonly router = inject(Router);

  /**
   * 当前路由 → 顶层 view 字符串（取 url 第一段）。
   * 使用 toSignal 把 router events 转为 signal，路由变化时自动重算。
   */
  private readonly currentUrl = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map((e) => e.urlAfterRedirects),
      startWith(this.router.url),
    ),
    { initialValue: this.router.url },
  );

  /**
   * 顶层 view：home / projects / project / notifications / settings / ...
   *
   * 2026-08-20 Epic 151 / Task 1310d 修复：
   * - 之前把 ``/projects`` 和 ``/project/:id/...`` 都映射为 ``'project'``，
   *   导致「项目」按钮（在 ``/projects``）和「工作台」按钮（在 ``/projects``）
   *   高亮语义错乱（「工作台」反被点亮）。
   * - 现在区分：``/projects`` → ``'projects'``；``/project/:id/...`` → ``'project'``。
   */
  readonly activeView = computed(() => {
    const url = this.currentUrl();
    const trimmed = url.replace(/^\/+|\/+$/g, '');
    const first = trimmed.split('/')[0] || '';
    if (first === '') return 'home';
    // 项目内（带 :id） → 'project'；项目列表 → 'projects'
    if (/^project\/\d+/.test(trimmed)) return 'project';
    if (first === 'projects') return 'projects';
    if (first === 'notifications') return 'notifications';
    if (first === 'settings') return 'settings';
    return first;
  });

  /**
   * 当前项目的 id（url 是 /project/:id/... 时取 :id）。
   * 移动端 "工作台" 入口仅在项目内可见。
   */
  readonly currentProjectId = computed<number | null>(() => {
    const url = this.currentUrl();
    const m = /^\/project\/(\d+)/.exec(url);
    return m ? Number(m[1]) : null;
  });
}
