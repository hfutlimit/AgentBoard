# Epic 150 / X3 supplement — Tab heading 补做踩坑（2026-08-20）

## 背景

playwright self-review 发现 X3 范围偏差：之前只改了顶层 view case 的 page-header，**没改 prototype 4-9 里 project 工作台 8 个 tab 内容组件的 heading**。

X3 supplement 范围：在 8 个 tab 内容组件顶部加 `<app-workspace-heading>`，对齐 prototype 03-09。

## 子 PR 列表

| PR | commit | tab | 命名 |
|---|---|---|---|
| PR 1 | 271d58a | overview-tab | h1「项目概览」+ 副标题 + 项目名 |
| PR 2 | 45c1932 | kanban-tab | h1「看板」+ count badge + 「仅看板标记」 + 「显示全部」 |
| PR 3 | 05ffdab | epics-tab | h1「Epics」+ 副标题 + 「+ 新建 Epic」 |
| PR 4 | b63887f | backlog-tab | **h1「工作项」**（原 Backlog 改名）+ 副标题 + count |
| PR 5 | 8d1da1e | proposals-tab / documents-tab / members-tab / app.html settings tab | 4 tab 一并 |

总计 8 个 tab 全覆盖（settings tab 在 app.html 内部，不是独立组件）。

## 关键坑

### 1. tab 内容组件 vs 顶层 view case 的区分

X3 「8 视图」= project 工作台 sidebar 的 8 个 tab（概览 / 看板 / Epics / 工作项 / 提案 / 文档 / 成员与 Agents / 设置），这些是 **project view 内部 activeTab 切的内容**（独立 tab 组件或在 app.html 内的 @if 块）。

之前 X3 改的是**顶层 view case 的 page-header**（projects / project / task / sprint / 等 10 个），**不是这 8 个 tab 的内容 heading** — 范围搞错。

**规则 → 「8 视图的 heading 改造」必看 prototype 截图定位 tab 实际位置**（在 sidebar 里的 = tab 组件，在 case 里的 = 顶层 view）。
**适用 → 后续 X4 / X5 任何「N 个视图改造」先 prototype 截图定位**。

### 2. 每个 tab 组件都加 import + imports 数组

每个 tab 组件（standalone）都要：
- `import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';`
- `imports: [..., WorkspaceHeadingComponent]`

7 个 tab 组件（overview / kanban / epics / backlog / proposals / documents / members）+ 1 个 app.html in-place（settings）= 8 处独立改。

### 3. 命名校准：Backlog → 工作项

当前 tab bar 显示「Backlog」标签（英文），heading 改成「工作项」（中文，对齐 prototype 09）。**两个命名不一致** — tab bar 标签也得改（todo list 里有「tab 命名校准」待做）。

### 4. Actions 按钮事件接口不全会跳过

overview-tab 没有 `createEpic` / `createProposal` 等事件，actions 按钮无法直接 emit。X3 阶段只补 heading，actions 待 X4 阶段扩展组件事件接口后补。

### 5. settings tab 在 app.html 内部

settings tab **不是独立组件**，是 app.html line 660-1033 的 @if (activeTab() === 'settings') 块。改法：直接在 settings 块内的 settings-layout 之前插入 `<app-workspace-heading>`。

## 验证记录

- vitest 全量：69/70 + 1 skip PASS（app.spec.ts + workspace-heading.spec.ts）
- E2E review 脚本（test_review_all_views.py）跑 10 个 view 截图：
  - _review_06_project.png（overview tab）：h1「项目概览」+ 副标题「项目 AgentBoard 的工作台、成员与最近交付」✓
  - _review_07_kanban.png（kanban tab）：h1「看板」+ count badge + 「仅看板标记」 + 「显示全部」✓
  - _review_08_epics.png（epics tab）：h1「Epics」+ 副标题「按业务目标组织项目交付。」+「+ 新建 Epic」✓
  - _review_09_backlog.png（work items tab）：h1「工作项」+ 200 count + 副标题「项目的任务、缺陷和交付状态。」✓
  - _review_10_project_settings.png（settings tab）：h1「项目设置」+ 副标题「仅影响当前项目，不改全局账号与 Agent 注册。」✓

## 已知差异（X4 / X5 待做）

1. **tab bar 标签 vs heading 命名不一致**：tab bar 显示「项目介绍 / Backlog」，heading 是「项目概览 / 工作项」 — 需要重命名 tab bar
2. **overview tab actions 缺失**：prototype 03 有「导出 / + 新建工作项」按钮，当前无（事件接口未补）
3. **settings tab 布局差异**：prototype 06 是单卡 full width，当前仍是左右双卡（设置子菜单 + 内容）
4. **stat 卡内容差异**：prototype 03 概览是 Sprint 完成 / 进行中 / 交付率 / Agent 利用率；当前是 Epics / 待办 / 看板 / 活跃提案

## 下一步

- X4：tab 命名校准（tab bar 改用 heading 一致的中文名）
- X5：actions 按钮扩展（overview-tab 加 create 事件）
- X6：settings tab 布局重构（单卡 full width）
- X7：overview tab stat 卡对齐 prototype
