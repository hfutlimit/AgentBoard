# 变更提案：任务列表「只看指派给我」快速筛选（Epic 33 v2.2）

## 背景
Epic 11 长期轨道要求「模仿 Jira、小步迭代」持续优化任务列表交互。当前任务列表已具备：
- 优先级快速筛选 chips（Epic 31 v2.0）
- 关键词搜索（Epic 35 v1.5）
- 分组（按状态/类型/负责人，Epic 29 v1.8）
- 高级筛选面板（状态/类型/逾期/标签）
- 排序（Task 730）

但缺少 Jira 中高频的「Assigned to me / 只看我」一键过滤——快速聚焦当前用户负责的工作项。

## 目标
在 Story 任务列表工具条新增「只看我」切换按钮：
- 点击后仅显示 `assignee_id == 当前登录用户` 的任务；
- 单选、与现有所有筛选（优先级 chips、搜索、分组、高级面板）取交集叠加；
- 选择状态 `localStorage` 持久化，刷新后保持；
- 「清除筛选」或再次点击可还原全部。

## 非目标
- 不引入新框架 / 构建链。
- 不改动后端 `models.py` / `api.py` 契约，纯前端增量。
- 不做「按任意成员筛选」（那是高级面板负责人维度的扩展，超出本次范围）。

## 范围
- 新增 `filterMineOnly` signal（读取 `localStorage.agentboard_filter_mine`）。
- 新增 `myUserId()` 计算（由 `currentUser()` + `members()` 映射得到当前用户 `user_id`）。
- 新增 `toggleFilterMine()` 方法（切换 + 持久化）。
- `visibleTasks` computed 叠加 assignee 过滤（仅在成员已加载且命中当前用户时生效，否则无操作，避免误伤）。
- `activeFilterCount` computed 计入该筛选，使既有「清除筛选」按钮与计数联动。
- `clearFilters()` 一并清除该筛选。
- 工具条新增「只看我」切换按钮（`.mine-toggle` 样式复用 `.qf-chip` 视觉语言）。
- 新增 Playwright e2e 覆盖：过滤生效、与计数联动、持久化、还原。

## 影响
- 仅 `frontend/src/app/{app.ts,app.html,app.css}`。
- 无后端改动，无迁移，无新端点。

## 退出标准
- 点击「只看我」后任务列表仅显示指派给当前用户的任务。
- 与优先级 chips / 搜索 / 分组 / 高级筛选叠加取交集。
- 刷新后筛选状态保持。
- 再次点击或「清除筛选」可还原。
- 成员未加载 / 当前用户不在成员列表时不误伤（无操作）。
- Playwright e2e 全绿（0 控制台 / 资源 / 页面错误）。
