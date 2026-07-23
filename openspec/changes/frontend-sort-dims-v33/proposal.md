# Proposal: 任务列表排序维度增强（v3.3）

## 问题
任务列表排序下拉当前仅暴露 5 个维度（创建/更新时间、优先级、标题、状态），
缺少「截止日期」与「指派人」两个高频维度。团队常需按 Deadline 或负责人梳理任务，
目前只能手动肉眼查找，效率低。

## 目标
在既有 `taskSortKey` / `visibleTasks` 排序基础设施上，扩展两个排序维度：
- **按截止日期（due_date）**：有日期任务按时间先后排列，无日期任务按标准语义置后（升序）/置前（降序）。
- **按指派人（assignee）**：按负责人姓名排序，未指派任务置后（升序）/置前（降序）。

## 非目标
- 不改变后端契约（纯前端 `visibleTasks` 计算）。
- 不引入分组/筛选新增维度（已有 chips 家族覆盖）。
- 不做多列组合排序（维持单键 + 方向）。

## 影响范围
- `frontend/src/app/app.ts`：`taskSortKey` 联合类型 + `visibleTasks` 排序分支 + 2 个私有比较助手 + `taskSortOptions` 增加 2 项。
- `frontend/src/app/app.html`：无需改动（下拉已 `@for (opt of taskSortOptions)` 渲染）。
- 用户偏好持久化复用既有 `localStorage.agentboard_sort_key` / `agentboard_sort_order`。
