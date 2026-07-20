# 变更提案：任务列表筛选结果引导（Epic 34 v2.3）

## 背景
Epic 11 长期轨道要求「模仿 Jira、小步迭代」持续优化任务列表交互。当前任务列表已具备：
- 优先级快速筛选 chips（Epic 31 v2.0）
- 关键词搜索（Epic 35 v1.5）
- 「只看我」快速筛选（Epic 33 v2.2）
- 分组（按状态/类型/负责人，Epic 29 v1.8）
- 高级筛选面板（状态/类型/逾期/标签）
- 排序（Task 730）

但存在两个体验缺口：
1. **筛选状态无可见的全局复位入口**：搜索框、优先级 chips、只看我已分别可控，但工具条没有一处统一的「清除全部筛选」按钮；用户只能逐个撤销，或去高级面板点「清除筛选」。
2. **空结果无法区分成因**：当筛选把任务全部过滤掉时，列表区与「本 Story 确实没有任何任务」渲染成同一个「暂无任务」，用户无法判断是「没任务」还是「筛选太严」。

## 目标
1. 工具条新增「清除全部筛选」按钮（`.clear-all-btn`）：当搜索框非空 **或** 任一筛选活跃（`activeFilterCount > 0`）时显示，点击一次性重置搜索 + 优先级 chips + 只看我 + 高级面板全部条件并清除本地持久化。
2. 筛选导致零结果时渲染友好的筛选空状态（`.filter-empty-state`）：区分「本 Story 无任务」（`.empty-inline`）与「筛选无匹配」，并提供「清除全部筛选」入口。

## 非目标
- 不引入新框架 / 构建链。
- 不改动后端 `models.py` / `api.py` 契约，纯前端增量。
- 不重置「分组 / 排序」等视图偏好（二者属视图排布而非筛选条件）。

## 范围
- 新增 `showClearAll` computed：`taskSearchQuery().trim() !== '' || activeFilterCount() > 0`。
- 新增 `clearAllFilters()` 方法：重置 `taskSearchQuery` 并复用 `clearFilters()`（覆盖优先级/类型/逾期/标签/只看我 + 持久化清除）。
- 工具条在高级筛选按钮前新增 `@if (showClearAll())` 包裹的「✕ 清除筛选」按钮。
- 任务列表 `@empty` 分支改造：按 `tasks().length` 区分渲染 `.empty-inline`（无任务）或 `.filter-empty-state`（筛选无匹配，内置「清除全部筛选」按钮）。
- 新增 CSS：`.filter-empty-state` / `.filter-empty-icon` / `.filter-empty-title` / `.filter-empty-desc` / `.clear-all-btn`。
- 新增 Playwright e2e 覆盖：按钮显隐、点击复位、筛选空状态、内部清除入口。

## 影响
- 仅 `frontend/src/app/{app.ts,app.html,app.css}`。
- 无后端改动，无迁移，无新端点。

## 退出标准
- 应用任一筛选后工具条出现「清除全部筛选」。
- 点击后所有筛选复位、任务恢复显示、搜索框清空、按钮消失。
- 构造零结果筛选时显示 `.filter-empty-state`（非「暂无任务」），且内部清除按钮可复位。
- 无 JS 报错 / 控制台错误 / .js+.css 404。
- Playwright e2e 全绿。
