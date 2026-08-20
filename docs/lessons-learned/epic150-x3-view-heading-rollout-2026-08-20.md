# Epic 150 / Story 324 (X3) — View heading rollout 踩坑（2026-08-20）

## 背景

X3 范围：用 `<app-workspace-heading>` 组件替换所有 view-level page-header。
- X2 PR 3（pilot）：settings view
- X3 PR 1（89944fd）：projects / documents / agents / proposals / notifications（list-style 5 个 view）
- X3 PR 2（82ed9e3）：project / task / sprint / proposal / admin（detail view 4 + admin 1）

总计 11 个 view-level page-header 改造完成。

## 关键坑

### 1. 原 page-header 模板嵌套不平衡（Angular parser 容忍但 AOT 不容忍）

原 `app.html` 多个 view 的 page-header 是：
```html
<div class="page-header">
  <div class="page-title-row">
    <h2>...</h2>
    <span class="badge ...">...</span>
  </div>
  <!-- 缺一个 </div> 收 page-header -->
```

实际只写了 1 个 `</div>` 收 page-title-row，page-header 的 `</div>` 缺失。Angular JIT 解析容忍，但 ng test AOT 编译报 NG5002。

**规则 → 替换 page-header 时**用 edit 工具的 old_string 精确包含整段开始到唯一 `</div>` 收尾**，避免多删或多留。
**适用 → 所有原模板有嵌套 div 但只写了 1 个收尾的场景。**

### 2. 顶层 view 多数 dead code（无 view.set 入口）

`@case ('agents') @case ('documents') @case ('proposals')` 三个 view case 当前 dead code：
- `view.set('agents')` 存在但 X1 PR 3 follow-up 隐藏外层 sidebar → 不可达
- `view.set('documents') / view.set('proposals')` 不存在
- E2E 验证不了这些 view 的 page-header 改造

**规则 → 改造 page-header 前先 grep `view.set('X')` 验证可达性；不可达 view 改造保留但 E2E 跳过。**

### 3. 单元测试 view 默认值 = 'home'，但 #sidebar / .logo-text 只在非 home 渲染

X1 PR 3 改 view 行为后，多个 app.spec.ts 测试失败（默认 view='home' → #sidebar 不渲染 → 测试找不到 .logo-text 等）。

**修复**：
```ts
app.view.set('projects');  // Epic 150 X1: home view hides outer topbar/sidebar
```

**规则 → view-based 单元测试在创建后立即 `view.set('projects')` 或其它非 home 视图。**

### 4. 单元测试测 dashboard DOM 但 dashboard 已删（X1 PR 2）

`should render dashboard delivery charts from live task data` 测试找 `.dashboard-analytics` `.activity-chart` 等，但 X1 PR 2 已删整个 `<div class="dashboard">`。

**修复** → 改测数据层 `app.dashboardStatusChart().segments.length`（计算函数还在，只是 template 不渲染）。

**规则 → DOM 测试要跟当前 template 状态同步；template 删了就把测试改成测数据层或 skip。**

### 5. /admin view 因 API 缺 /me 端点不可达

`loadRoute` 调 `this.adminMe()` → `this.api.me()` → 404 → 返回 null → view='admin' 被重定向到 /。

**规则 → E2E 跳过 API 不支持的 view；生产 API 加 /me 后可恢复测试。**

### 6. ng serve 编译通过 ≠ vitest AOT 编译通过

`ng serve` 用 JIT 编译（容忍度高），`ng test` 用 AOT 编译（严格）。PR 1/2 推完后 E2E 全过，但 vitest 失败才暴露模板嵌套问题。

**规则 → 每次改模板后跑 vitest 验证 AOT 编译。**`ng test --runner-config vitest.config.ts --watch=false`

### 7. unit test 修改 fixture input 用 autoDetectChanges

之前 X2 PR 2 学到的：改 input 后 manual detectChanges + 同步 whenStable 容易 NG0100。

**应用**：
```ts
fixture.autoDetectChanges();
await fixture.whenStable();
```

## 验证记录

### X3 PR 1 (test_x3_pr1_list_views.py)
- 2 个可达 view (projects / notifications) PASS
- 3 个 dead-code view (agents / documents / proposals) 跳过 E2E
- home view 回归：workspace-heading 不渲染 ✓

### X3 PR 2 (test_x3_pr2_detail_views.py)
- 1 个可达 view (/project/3) PASS — h1=AgentBoard, 2 badges (AGB + 🔒 邀请制), 无 legacy page-header
- /admin 跳过（API 缺 /me）
- task / sprint / proposal 跳过（需动态 id）
- home view 回归：workspace-heading 不渲染 ✓

### vitest 全量 (vitest.config.ts)
- 3 test files passed
- 69 tests passed + 1 skipped（app.spec.ts:1 skip 修 X1 PR 后没意义）
- AOT 编译成功（修了 1 个 stray `</div>` + 5 个 view default home 测试 + 1 个 dashboard DOM 测试）

## 视觉效果对比

### X3 PR 1 (projects view)
改造前：`<div class="page-header"><div class="page-title-row"><h2>项目中心 <span class="project-count-badge">11</span></h2>...</div></div>`
改造后：`<app-workspace-heading eyebrow="PROJECT CENTER" title="项目中心" subtitle="..."><span class="heading-title-badge project-count-badge">11</span><button class="heading-action-btn btn-primary" (click)="...">＋ 新建项目</button></app-workspace-heading>`

视觉对比：完全一致（h1 + badge 同行、副标题 muted、按钮右上）。结构由组件统一管理，未来调整一处生效所有 view。

### X3 PR 2 (project view 顶部)
改造前：h2 + 2 badges（AGB + 🔒 邀请制）
改造后：app-workspace-heading + 2 heading-title-badges（AGB + 🔒 邀请制）— 视觉一致

## 下一步

- 后续可批量改 card 内部 section-header (h3) → 统一组件（X4 范围）
- epic / story view 的 detail-header 不是 page-header（card 内部），可考虑改用 `<app-section-header>` 子组件
- agents / documents / proposals 顶层 view 重新加入口（恢复 view.set 调用 + sidebar 按钮）
- admin view 修 API 加 /me 端点或换 adminMe 路径
