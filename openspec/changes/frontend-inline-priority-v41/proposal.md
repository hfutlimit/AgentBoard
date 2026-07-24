# Proposal: 任务列表行内快速修改优先级 (v4.1 / Epic 54)

## 问题
任务列表每行已渲染优先级徽章（`.badge.priority.priority--{x}`），但仅作展示，改变优先级必须进入批量面板操作。对于「把这条任务的优先级从 高 改成 紧急」这类高频轻量操作，路径过重，不符合 premium 任务列表的交互预期（参考 Linear / Jira 行内改优先级）。

行内交互家族已具备：v3.4 行内快速状态切换、v3.8 行内快速指派、v3.9 行内快速编辑截止日期 —— 唯独缺「优先级」。补齐第 4 件，使行内交互家族完整。

## 目标
将任务行的优先级徽章升级为可点击的「快速优先级编辑器」，与前三者对称：
- 点击优先级徽章弹出 fixed 浮层，列出全部 5 档优先级（复用既有 `priorities` 信号 + `priorityLabel`）
- 选择即调用既有 `api.updateTask(id, { priority })` 端点并即时更新行内优先级（即时反馈，无需刷新）
- 当前优先级 `active` 高亮
- 点击背景遮罩可关闭浮层
- 纯前端实现，零后端契约变更

## 非目标
- 不改动后端优先级逻辑 / `updateTask` 端点（`priority` 已在 `TaskPatch` 允许字段集）
- 不做多选批量改优先级（批量改优先级已在 bulk 面板提供，见 v2.9）
- 不引入新的优先级档位

## 风险
低。复用既有 `api.updateTask`、`priorities` 信号、`priorityLabel`、`tasks` 信号；仅新增浮层交互、少量信号与少量 CSS。fixed 定位浮层规避滚动容器裁剪（与 v3.4 一致）。

## 交互对齐
- 浮层优先级列表来源 = `priorities`（`['highest','high','medium','low','lowest']`），与批量改优先级面板一致
- 优先级变更动作即时 `tasks.update` 局部刷新 + `notify` 提示，与 v3.4 `quickSetStatus` / v3.8 `quickAssign` 行为对称
- 不依赖 `members()`，无网络懒加载（优先级是本地枚举，全部已知）
