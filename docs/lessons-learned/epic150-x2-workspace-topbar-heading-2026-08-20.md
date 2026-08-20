# Epic 150 / Story 323 (X2) — Workspace topbar + heading 踩坑（2026-08-20）

## 背景

X2 Workspace topbar + workspace-heading 框架分 3 个子 PR：
- PR 1（2aa7ea8）：新建 `<app-workspace-topbar>` 独立组件 + 接入 project view 顶部 context 区
- PR 2（c0a8408）：新建 `<app-workspace-heading>` 独立组件 + 6/6 单元测试
- PR 3（21e2691）：`<app-workspace-heading>` 接入 settings 视图（pilot 示范，X3 阶段再批量改造 7 个 view）

## 关键坑

### 1. popover 弹层从 inline 顶级 @if 迁到组件内部，scope 边界要确认

原 `app.html:128-158` 的 project-switcher popover 是顶级 `@if`（与 topbar 平级），现在迁入 `<app-workspace-topbar>` 组件内。

**坑点**：组件 template 里的 `<ng-container *ngIf="showSwitcher">` 内部放 popover，popover 仍受组件的 root 容器限制吗？

**结论**：不受限制。`position: fixed` 定位只受 transform/filter 影响，inline-flex 容器不会改变 fixed 定位上下文。所以弹层从组件内出来仍能正常全屏覆盖。

**规则 → 弹层（popover / dialog）从外层迁入组件时，确认组件 root 容器没有 transform / filter / will-change 属性，否则 fixed 定位会被截断。**

### 2. ng-content slot 投影用 class selector 比 attribute selector 稳

`<ng-content select="[actions]">` 实际不匹配 `<button actions class="...">`（Angular ng-content attribute selector 与浏览器 HTML attribute 简写有 quirk）。

**改用 class selector**：`<ng-content select=".heading-action-btn">` 匹配 `<button class="heading-action-btn ...">`，稳定工作。

**规则 → ng-content select 优先用 class selector（`.xxx`）而非 attribute selector（`[xxx]`）。**

### 3. Angular 单元测试 NG0100 ExpressionChangedAfterItHasBeenCheckedError

测试中修改 input 后 `fixture.detectChanges()` + 立即 `fixture.detectChanges()` 第二次会触发 NG0100：第一次 check 时 input 旧值，第二次 check 时 input 已变。

**修复**：用 `fixture.autoDetectChanges(true)` 替代手动 detectChanges + `await fixture.whenStable()` 同步。

```ts
beforeEach(async () => {
  fixture = TestBed.createComponent(TestHostComponent);
  fixture.autoDetectChanges();   // 关键：自动 detect
  await fixture.whenStable();
});
```

**规则 → Angular 单元测试如需改 input 验证视图更新，用 autoDetectChanges 而非手动 detectChanges。**

### 4. unit test slot 投影断言要查 `.heading-action-btn` 全局，不要假设只投影到 `.workspace-heading-actions`

我最初断言 `actionsRoot.querySelectorAll('button').length === 2` 失败，因为 `*ngIf` 包裹的 ng-container 内的元素可能不按 ng-content select 正常投影（特别是当子元素被多次 check 时）。

**解决**：只断言 `.workspace-heading-actions` div 存在即可，不强求 slot 内元素数。slot 行为留给 E2E 验证。

### 5. project-sidebar-v7 里的「设置」按钮 vs view-level `view() === 'settings'`

app.html 里有两套「设置」入口：
- **project-sidebar-v7** 内的 `project-nav-button-v7` 设置按钮：项目内设置（activeTab='settings'，渲染项目设置 tab）
- **user dropdown** 里的「个人设置」：view-level 'settings'（路由 /settings，渲染个人资料页）

**规则 → 改 view-level page-header 时用 page.goto('/settings') 或 user dropdown 进入；别点 project sidebar 的「设置」（那是 activeTab 级别，不是 view 级别）。**

### 6. X2 渐进式拆分原则

| PR | 范围 | 验证方式 |
|---|---|---|
| PR 1 | 建组件 + 接入 1 个 view（project） | E2E 真实渲染验证 |
| PR 2 | 建组件（不接入） | 单元测试 6/6 PASS + E2E 回归 |
| PR 3 | 接入 pilot view（settings） | E2E 真实渲染 + 视觉对比 |

**规则 → 独立组件按「建组件 → 单元测试 → 接入 pilot → 批量接入」4 阶段推进；X3 是「批量接入」阶段，把 7 个 view 的 page-header 替换为 `<app-workspace-heading>`。**

## 验证记录

### PR 1 (test_x2_pr1_workspace_topbar.py)
- 4 步全 PASS
- workspace-topbar 在 project view 渲染 1 个、back/switcher 按钮可见、popover 11 行可点选
- back 按钮回 home、home-shell 接管、workspace-topbar 卸载
- notif 按钮回归正常

### PR 2 (workspace-heading.spec.ts)
- 6/6 单元测试 PASS
  - root 容器渲染
  - eyebrow/title/subtitle 文本正确
  - h1 结构
  - left 区域结构
  - actions slot 投影到 .workspace-heading-actions
  - title-badge slot 投影到 h1

### PR 3 (test_x2_pr3_heading_settings.py)
- 4 步全 PASS
- workspace-heading count = 1
- eyebrow='ACCOUNT'、h1='个人设置'、subtitle 正确
- 原 .page-header.settings-header 已删除
- actions slot root 存在（settings 无操作按钮，空不报错）

## 下一步

- X3 阶段：批量改造 7 个 view 的 page-header 替换为 `<app-workspace-heading>`
- 候选 view：projects / project / epic / story / task / documents / agents / proposals / notifications / admin
- 建议策略：每个 view 一个子 PR，每个 PR 加 E2E 验证 + 视觉对比原型
