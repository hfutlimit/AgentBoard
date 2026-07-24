# Proposal: 任务列表行内快速指派 (v3.8 / Epic 51)

## 问题
任务列表每行已渲染指派人头像（`.assignee-avatar-sm`），但仅作展示，改变指派必须进入任务详情页或在批量面板里操作。对于「把这条任务改派给某人 / 取消指派」这类高频轻量操作，路径过重，不符合 premium 任务列表的交互预期（参考 Linear / Jira 行内指派）。

## 目标
将任务行的指派人头像升级为可点击的「快速指派器」，与 v3.4 行内快速状态切换对称：
- 点击指派人头像 / 未指派徽章弹出 fixed 浮层，列出**当前项目成员**（复用既有 `members()` 信号）
- 选择成员即调用既有 `api.updateTask(id, { assignee_id })` 端点并即时更新行内指派人（即时反馈，无需刷新）
- 提供「未指派」选项用于取消指派
- 点击背景遮罩可关闭浮层
- 纯前端实现，零后端契约变更

## 非目标
- 不改动后端指派逻辑 / `updateTask` 端点
- 不做多选批量指派（批量指派已在 bulk 面板提供）
- 不引入新的角色 / 权限维度

## 风险
低。复用既有 `api.updateTask`、`members()` 信号、`getAssigneeName` / `getAssigneeInitials`、`tasks` 信号；仅新增浮层交互、少量信号与少量 CSS。fixed 定位浮层规避滚动容器裁剪（与 v3.4 一致）。

## 交互对齐
- 浮层成员列表来源 = `members()`（项目成员），与批量指派面板一致
- 若当前 `members()` 为空，打开浮层时按 `task.project_id` 懒加载（`loadMembers`），保证任何入口都有数据
- 指派动作即时 `tasks.update` 局部刷新 + `notify` 提示，与 v3.4 `quickSetStatus` 行为对称
