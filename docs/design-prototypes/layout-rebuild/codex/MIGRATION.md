# AgentBoard 前端布局重建迁移契约（MIGRATION.md）

> Epic 149 · Story 316（阶段 0 冻结设计契约）
> 来源原型：`docs/design-prototypes/layout-rebuild/codex/agentboard-home-workspace.html`（v7 设计基准）
> 目标：将 v7 静态原型冻结为可执行的迁移契约，为后续阶段（Home / Workspace 重构）提供组件边界与路由映射依据。

---

## 1. 组件边界划分

静态原型是一个单文件 HTML，包含三层界面：**Home（首页）**、**Workspace（项目工作台）**、**Managed List（可复用列表）**。迁移到 Angular 时按以下边界拆分为独立组件。

### 1.1 `HomeComponent`（对应 `homeShell`）

| 原型锚点 | 原型结构 | Angular 映射 |
| --- | --- | --- |
| 顶层容器 | `<section class="home-shell" id="homeShell">` | `HomeComponent`（路由 `''` 或 `/home`） |
| 品牌区 | `.brand` + `.brand-mark` | `AppBrandComponent`（全局复用，含 `--brand-mark-accent` 双圆） |
| 首页导航 | `.home-nav` + `.home-nav-button[data-home-route]` | `HomeComponent` 内部 `homeRoute` signal（`projects` / `agents`） |
| 项目视图 | `.home-view[data-home-view="projects"]` | `HomeProjectsViewComponent`（子路由 `projects`） |
| Agents 视图 | `.home-view[data-home-view="agents"]` | `HomeAgentsViewComponent`（子路由 `agents`） |
| 项目行 | `.project-master-row[data-project]` | `ManagedListComponent` 的列表项 |
| 进入工作台 | `#enterWorkspaceButton`（`setWorkspaceProject` → 切 workspace） | `router.navigate(['/workspace', projectId])` |

> Home 层本身不持有项目详情的编辑逻辑，详情预览（`.project-detail`）作为 `HomeProjectsViewComponent` 的局部状态。

### 1.2 `WorkspaceComponent`（对应 `projectWorkspace`）

| 原型锚点 | 原型结构 | Angular 映射 |
| --- | --- | --- |
| 顶层容器 | `<section class="workspace-shell" id="projectWorkspace">` | `WorkspaceComponent`（路由 `workspace/:projectId`） |
| 返回首页 | `#backToHome`（`aria-label="返回全部项目"`，home 图标语义锚点） | `router.navigate(['/home'])` |
| 项目切换器 | `#projectSwitcherButton` + `#projectSwitcher` popover | `ProjectSwitcherComponent`（独立 popover） |
| 侧边栏 | `.project-sidebar` + `.sidebar-project` | `WorkspaceSidebarComponent` |
| 8 个路由导航 | `.project-nav-button[data-workspace-route]`，共 8 项 | `WorkspaceNavComponent` → 驱动 `<router-outlet>` |
| 内容区 | `.workspace-main` + `.workspace-view[data-workspace-view]` | `WorkspaceComponent` 的 `<router-outlet>` 懒加载子路由 |

**8 个 lazy routes（原型 `data-workspace-route` 枚举，顺序固定）：**

| # | route | 原型 view | 懒加载模块 |
| --- | --- | --- | --- |
| 1 | `overview` | `概览` | `OverviewModule` |
| 2 | `kanban` | `看板` | `KanbanModule` |
| 3 | `epics` | `Epics` | `EpicsModule` |
| 4 | `workitems` | `工作项` | `WorkItemsModule` |
| 5 | `proposals` | `提案` | `ProposalsModule` |
| 6 | `documents` | `文档` | `DocumentsModule` |
| 7 | `members` | `成员与 Agents` | `MembersModule` |
| 8 | `settings` | `设置` | `SettingsModule` |

### 1.3 `ManagedListComponent`（独立复用列表）

原型中多处出现"主从列表"模式，应抽离为独立组件，避免每个视图重复实现：

| 原型出现位置 | 含义 | 由 `ManagedListComponent` 提供的能力 |
| --- | --- | --- |
| `.project-master`（Home 项目列表） | 项目主列表 | 选中态、虚拟滚动、过滤、空态 |
| `.agent-table`（Agents 视图） | Agent 表格 | 列定义、排序、状态徽标 |
| `.workspace-view` 中的卡片网格（overview 最近活动 / 工作项卡片） | 通用卡片列表 | 模板投影（`<ng-content>`）、分页 |

`ManagedListComponent` 对外暴露 `@Input() items`、`@Input() trackBy`、`@Output() itemSelect`，内部管理选中索引与键盘可达性（`role="listbox"` / `aria-selected`）。

---

## 2. 静态 `hidden` 切换 → Angular 子路由懒加载映射

原型依赖三类切换机制，迁移时需分别替换：

### 2.1 顶层两层切换（Home ↔ Workspace）

**原型实现：**
```js
// 进入 workspace
document.getElementById('homeShell').hidden = true;
document.getElementById('projectWorkspace').classList.add('active');
// 返回 home
document.getElementById('projectWorkspace').classList.remove('active');
document.getElementById('homeShell').hidden = false;
```

**Angular 映射：** 不再用 `hidden` 互斥，改为两级路由：
- `homeShell` → 路由 `home`（懒加载 `HomeModule`）
- `projectWorkspace` → 路由 `workspace/:projectId`（懒加载 `WorkspaceModule`）
- 由 `AppRoutingModule` 的 `<router-outlet>` 决定当前渲染哪一层，天然互斥。

### 2.2 同层多视图切换（home-view / workspace-view）

**原型实现：** `.home-nav-button` / `.project-nav-button` 通过 `classList.toggle('active')` + `.home-view[data-home-view].active { display:block }` 切换。

**Angular 映射：**
- Home 的 `projects` / `agents` → `HomeComponent` 内嵌套子路由（`home/projects`、`home/agents`），分别懒加载 `HomeProjectsViewComponent` / `HomeAgentsViewComponent`。
- Workspace 的 8 个 route → `WorkspaceComponent` 内嵌套子路由（见 §1.2 表），全部 `loadChildren` 懒加载。
- 激活态由 `routerLinkActive="active"` 驱动，与原型 `.active` class 一一对应。

### 2.3 Popover / 浮层切换

**原型实现：** `switcher` / `notificationPanel` / `createProjectPanel` 用 `aria-hidden` + `setHidden()` 控制显隐，并依赖 `closeTransient()` 全局关闭。

**Angular 映射：**
- 改用 `*ngIf` / `@if` 控制浮层，或 `CdkOverlay`（`@angular/cdk/overlay`）托管定位与点击外部关闭。
- 关闭逻辑由 Overlay 的 `Dispose` / `BackdropClick` 自动处理，替代手写的 `closeTransient()` + `document` 监听。

### 2.4 映射总览

| 原型机制 | 选择器 / 属性 | Angular 等价 |
| --- | --- | --- |
| 两层切换 | `homeShell.hidden` / `projectWorkspace.hidden` | 一级路由 `home` vs `workspace/:id` |
| Home 子视图 | `.home-view.active` | 二级路由 `home/projects` / `home/agents` |
| Workspace 子视图 | `.workspace-view.active`（8 项） | 二级懒加载路由（8 项） |
| 激活态 | `.active` class | `routerLinkActive="active"` |
| 浮层 | `aria-hidden` + `setHidden()` | `*ngIf` / `CdkOverlay` |
| 全局关闭 | `closeTransient()` + `document` click | Overlay 自动 dispose |

---

## 3. navy ↔ indigo 令牌映射表

原型 v7 采用 **navy 主色体系**（`:root` 中 `--navy: #10243e`、`--blue: #2864dc`），而现有生产前端（`src/styles.css`）采用 **indigo 品牌体系**（`--brand-500: #4f46e5`）。迁移时按以下规则对齐，确保品牌识别与对比度。

### 3.1 保留的 indigo 资产

| 令牌 | 现有值 | 决策 | 理由 |
| --- | --- | --- | --- |
| `--grad` | `linear-gradient(135deg, #6366f1 0%, #8b5cf6 55%, #a855f7 100%)` | **保留** | 唯一签名渐变，是品牌视觉锚点；不并入 navy 体系，避免品牌辨识度丢失 |
| `--violet` | `#7c3aed` | **保留** | 与 `--grad` 同族，用于强调态（如 Proposals 提案徽标） |

### 3.2 替换为 navy 体系的资产

| 现有 indigo 令牌 | 现有值 | navy 体系目标令牌 | navy 目标值 | 使用位置 |
| --- | --- | --- | --- | --- |
| `--brand-500` | `#4f46e5` | `--blue` | `#2864dc` | 主操作、链接、选中态 |
| `--brand-600` | `#4338ca` | `--blue-dark` | `#174db3` | 主操作 hover / 按压 |
| `--brand-700` | `#3730a3` | `--navy` | `#10243e` | 侧边栏背景、标题强调 |
| `--brand-soft` | `#eef2ff` | `--blue-soft` | `#eaf1ff` | 选中态浅底 |
| `--brand-ring` | `rgba(79,70,229,.18)` | `rgba(40,100,220,.18)` | `rgba(40,100,220,.18)` | focus ring |
| `--sh-brand` | `rgba(99,102,241,.45)` | `rgba(40,100,220,.40)` | `rgba(40,100,220,.40)` | 品牌阴影 |
| `--primary` | `var(--brand-500)` | `var(--blue)` | — | 别名重定向 |
| `--primary-hover` | `var(--brand-600)` | `var(--blue-dark)` | — | 别名重定向 |

### 3.3 原型新增 design-token（Story 316 / P1 修复引入）

| 令牌 | 值 | 用途 | 对应 P1 修复 |
| --- | --- | --- | --- |
| `--blue-bright` | `#64a1ff` | Workspace 侧边栏激活态指示条（`project-nav-button.active::before`） | P1-2 |
| `--brand-mark-accent` | `#1fc9a3` | 品牌标记双圆中的强调绿圆（`.brand-mark::after`），提升与白圆的对比度 | P1-3 |

> 所有新增/替换令牌集中在 `:root`（及 Angular 的 `styles/_tokens.scss`）统一管理，禁止在组件内硬编码色值。

---

## 4. 验收清单（对应 Task 1281 QA）

1. **P1 修复验证**
   - [ ] P1-1：`#backToHome` 按钮图标为 home 语义（`<use href="#i-home"/>`，原 `#i-back`），且带 `aria-label="返回全部项目"`。
   - [ ] P1-2：侧边栏激活指示条色值来自 `var(--blue-bright)`（不再硬编码 `#64a1ff`）。
   - [ ] P1-3：品牌标记绿圆来自 `var(--brand-mark-accent)`，与白圆对比度提升。
2. **MIGRATION.md 完整性**
   - [ ] 覆盖 `home`→`HomeComponent`（projects/agents 两 view）、`workspace`→`WorkspaceComponent`（8 lazy routes）、`managed-list`→`ManagedListComponent` 三类映射。
   - [ ] `hidden` 切换 → Angular 子路由懒加载的映射路径清晰（§2.4 总览表）。
3. **令牌映射表**
   - [ ] navy↔indigo 映射表无遗漏，`--grad` 渐变明确标注为保留，`--brand-*` 明确标注替换为 navy 体系。

---

*本契约为阶段 0 冻结版本（v7）。后续阶段（Story 317+）实现须以本文件为基准，新增令牌须回填 §3.3。*
