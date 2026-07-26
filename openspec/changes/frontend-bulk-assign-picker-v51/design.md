# Design: 批量指派面板成员头像/姓名选择器 + 搜索（v5.1）

## 现状
- 批量操作区（`@if (selectedTaskCount > 0)` 的 `.bulk-action-bar`）的「批量指派」按钮调用 `showBulkActionPanel('assignee')`。
- 原 `assignee` 面板用 `<select class="bulk-assignee-select">` 渲染 `members()`（含空选项「未指派」），「应用」按钮调用 `bulkUpdateAssignee(bulkAssigneeId())`。
- `members()` 在 Story 视图加载时已通过 `loadMembers(epic.project_id)` 填充（api.app.ts 行 1398），数据可用。
- 行内改指派（v3.8）已用 `.assign-menu` 浮层渲染「头像 + 姓名」成员列表，含 `.assignee-avatar-sm`（首字母缩写圆形头像）与 `getAssigneeName` / `getAssigneeInitials`。

## 目标
1. 以 chip 列表替代 `<select>`，每个成员一个 `.bulk-member-chip`（头像 + 姓名），保持 Jira 式紧凑体验。
2. 顶部搜索框按 `username` 实时过滤成员列表；无匹配时显示「无匹配成员」空态。
3. 点击成员 chip 即时批量指派并收起面板（与 status/priority 面板「点击即应用」一致）；「未指派」chip 即时清除指派。
4. 复用现有主题变量与头像组件，dark 模式自适应。

## 关键设计决策
- **即时应用而非二次确认**：与 status/priority 批量面板保持一致，点击即执行，减少操作步骤。
- **搜索过滤置于前端**：`filteredBulkMembers()` 在 `members()` 上做 `username` 子串匹配，零后端交互。
- **面板内联渲染而非浮层**：相比 v3.8 浮层，批量面板本就在 `.bulk-action-bar` 下方，内联成员列表更直观、可滚动，避免遮挡。
- **`bulkAssignSearch` 在开/关面板时重置**：防止搜索词在多次批量操作间残留造成困惑。
- **零契约变更**：完全复用 `bulkUpdateAssignee()`（内部已做 `clearTaskSelection` + `refresh`），不新增/修改任何 API。

## 数据/状态流
```
用户点击「批量指派」
  → showBulkActionPanel('assignee')  // 重置 bulkAssignSearch=''
  → 渲染 .bulk-panel--assignee
      输入搜索 → bulkAssignSearch.set(v) → filteredBulkMembers() 重算
      点击成员 chip → bulkAssigneeId.set(id); bulkUpdateAssignee(id); closeBulkActionPanel()
                    → (API) bulkUpdateTasks(ids,{assignee_id}) → 局部刷新 + 清选
      点击「未指派」 → bulkAssigneeId.set(null); bulkUpdateAssignee(null); closeBulkActionPanel()
                    → (API) bulkUpdateTasks(ids,{clear_assignee:true})
```

## 测试策略
- E2E：登录 admin → /story/{seedStory} → 勾选任务 → 批量指派 → 断言 chip 数/头像数、搜索过滤（qa1→1、zzzz→空态）、点击 qa1 chip 经 API 复核 assignee_id、点击「未指派」复核清空、0 控制台/页面/404 错误。
- 回归：后端 `test_epic30_cache.py`（8 passed）+ 前端 v5.0 详情 Markdown / v4.5 抽屉导航 E2E 全绿，确认构建无回归。
