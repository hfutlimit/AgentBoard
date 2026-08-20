# Epic 151 / Story 328 / X.B2 响应式 + a11y 踩坑

**日期**：2026-08-20
**Story**：328（响应式 + 无障碍 a11y）
**Commit**：`51d8bc6 feat(Epic151): Story 328 - responsive 840px + a11y aria-label/focus-trap/bottom-tab-bar`
**E2E**：`tests/e2e_epic149/test_x_b2_responsive_a11y.py` — 5 视口 + focus trap 5/5 PASS

## 背景

Story 327 删了旧 emoji tab-bar 后，840px 以下视口无可见项目入口；同时 Epic 149 静态
Review 高优先级 #6 要求补响应式 + a11y。范围：

- 1160px 断点：sidebar 隐藏文字后补 `aria-label`
- 840px 断点：新增 `BottomTabBarComponent`（移动端 5 item nav）
- focus trap：抽 `FocusTrapDirective` 应用到 6 modal
- 修复 `validateAuth()` 关键 bug

## 踩坑（按出现顺序）

### 1. `validateAuth()` token 有效时必须 `authVisible.set(false)`

**症状**：E2E 重启后注入 token → reload，URL 仍被拉回 `/login`，即使 token 有效。

**根因**：`validateAuth()` 走 `try { me = await api.me() }` 成功路径时只设了 `isAdmin()` /
`localStorage`，**没清 `authVisible()`**。前一次 `showLogin()` 留下的 `authVisible=true`
状态被保持，导致 `loadRoute()` 在 `app.ts:2029` 提前 return，主内容区被登录 modal 遮挡。

**修复**：`app.ts:1554` `validateAuth()` 成功路径加一行 `this.authVisible.set(false)`。

**教训**：用 signal 管理「modal 是否可见」时，所有「关闭」路径必须显式 `set(false)`。
reload 不会重置 Angular signal，只会重置组件实例——状态机不变量必须由代码显式维护。

### 2. `BottomTabBarComponent` 第 5 个 item 不能 `*ngIf` 在 home view

**症状**：第一版 `*ngIf="currentProjectId()"` 让 5 item 在 home 变成 4 item，E2E 期望 5。

**根因**：移动端用户即使在 home 也需要"工作台"入口跳转到 `/projects`（无当前项目时
的 fallback），不能用 `*ngIf` 隐藏。

**修复**：第 5 item 永远渲染，routerLink 改为三目：
`[routerLink]="currentProjectId() ? ['/project', id, 'overview'] : ['/projects']"`，aria-label
也根据状态动态切换 `当前项目工作台` ↔ `工作台`。

**教训**：mobile-first 设计不要因为"上下文不存在"就隐藏入口，要提供 fallback 路由。

### 3. `[attr.aria-current]="..."` 必须用 attr binding

**症状**：`<a routerLink="..." ariaCurrentWhenActive="...">` 无效，DevTools 看不到 `aria-current`。

**根因**：`ariaCurrentWhenActive` 是 RouterLink directive 的 input，**不是** HTML attribute。
Angular 模板里要用 `[attr.aria-current]="active ? 'page' : null"` 显式 attribute binding，
值用 `null` 而非 `false`（后者会渲染为字符串 "false"）。

**教训**：native ARIA 属性永远用 `[attr.X]` 形式，**不要**依赖 directive 的语义化 input。

### 4. FocusTrapDirective 的 `onEscape` 参数类型必须是 `Event`

**症状**：第一版写 `@HostListener('keydown.escape', ['$event']) onEscape(e: KeyboardEvent)`，
TS 报 TS2345 类型不匹配。

**根因**：Angular 的 `@HostListener` 把 `$event` 绑定为**通用 `Event`**，需要在方法体里 cast
到 `KeyboardEvent`（用 `(e as KeyboardEvent).key`）。直接声明 `KeyboardEvent` 编译不过。

**教训**：Angular HostListener 的 `$event` 总是 `Event`，模板类型是建议而非强制。

### 5. E2E 中 `log("  -", f)` TypeError

**症状**：测试结束时 `for f in failures: log("  -", f)` 抛 `TypeError: log() takes 1 positional argument but 2 were given`。

**根因**：`log(msg, *, flush=True)` 只接受 1 个位置参数。PowerShell 习惯写 `Write-Host "  -" $f`
分两个参数，但 Python 风格必须 `log(f"  - {f}")` 一个 f-string。

**教训**：跨语言写 helper 时检查签名；Python 默认单参数 helper 不要试图模仿 PowerShell 的
多参数 `Write-Host`。

### 6. `<svg>` 必须 `aria-hidden="true"` 避免冗余

**症状**：屏幕阅读器会读 "image 概览"，与旁边 `<span>概览</span>` 重复。

**根因**：a11y 装饰性 svg（lucide / heroicons 风）默认会被读屏识别为 image。

**修复**：8 navy tab / bottom-tab-bar 所有 `<svg class="icon-v7">` 加 `aria-hidden="true"`。

**教训**：纯装饰 svg 必须显式 `aria-hidden`，否则和文本标签重复读屏。

### 7. mobile_375 视口下 navy project-sidebar 不能保留 display

**症状**：CSS `@media (max-width: 840px) { .project-sidebar-v7 { display: none } }` 不生效。

**根因**：组件用了 `ViewEncapsulation.None`，host component 的 styles 仍受 .layout 的
`grid-template-columns: 240px 1fr` 约束，navy sidebar 隐藏在 grid 内但仍占位。

**修复**：同时改 `.layout.has-project-sidebar` 的 `grid-template-columns: 1fr` 让主内容区
撑满。两者必须同时改才能"既隐藏又不再占位"。

**教训**：CSS Grid 布局 + display:none 不会自动释放列宽——要改 grid-template-columns 才行。

## 关键设计决策

1. **BottomTabBarComponent 5 item 而非 6+**：首页 / 项目 / 工作台 / 通知 / 我的。
   "工作台"槽位 fallback 到 /projects（无当前项目时），保持 5 item 不变。

2. **FocusTrap 抽 standalone directive 而非 service**：modal/popover/dialog 用法
   一致，directive 形式让模板声明即可（`appFocusTrap (appFocusTrapEscape)="closeX()"`），
   比 service 注入更直观。

3. **modal aria-labelledby 全部用 `<h3 id="...">`**：和现有视觉层级一致（modal header
   一直是 h3），屏幕阅读器读"对话框，标题：xxx"自然。

4. **840px 而非 768px 断点**：iPad portrait 768px 仍按 tablet 看（保留 navy sidebar 收窄），
   只有更窄的 Android/iPhone 触屏才转 mobile bottom-tab-bar 模式。

## 后续可优化

- ProjectSwitcher popover 还没接 FocusTrap（不属于 6 modal 范围，留 Story 329+）
- Command palette 同上
- 移动端 375px 视口下 overview tab 的 stat 卡目前是 2 列（`repeat(2, 1fr)`），更窄
  可考虑 1 列（320px 及以下）
- vitest 单测覆盖 FocusTrapDirective（当前只有 E2E 验证）
