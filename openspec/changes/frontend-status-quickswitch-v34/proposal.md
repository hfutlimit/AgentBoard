# Proposal: 任务列表行内快速状态切换 (v3.4 / Epic 47)

## 问题
任务列表每行已渲染状态徽章，但仅作展示，改变状态必须进入任务详情页或拖拽看板卡片。对于「把这条任务标为进行中 / 完成」这类高频轻量操作，路径过重，不符合 premium 任务列表的交互预期（参考 Linear / Jira 行内状态切换）。

## 目标
将任务行的状态徽章升级为可点击的「状态快速切换器」：
- 点击状态徽章弹出 fixed 浮层，仅展示**合法的目标状态**（前端镜像后端 `TRANSITIONS` 状态机）
- 选择目标即调用既有 `setTaskStatus` 端点并即时更新行内状态（即时反馈，无需刷新）
- 点击背景遮罩可关闭浮层
- 纯前端实现，零后端契约变更

## 非目标
- 不改动后端状态机 / `setTaskStatus` 端点
- 不做多选批量状态切换（批量状态已在 bulk 面板提供）
- 不引入新的状态维度

## 风险
低。复用既有 `api.setTaskStatus`、`statusLabel` / `statusColor`、`tasks` 信号；仅新增前端状态机镜像、`status-pill` 交互、`status-menu` 浮层与少量 CSS。fixed 定位浮层规避滚动容器裁剪。

## 状态机对齐
后端 `TRANSITIONS`：`BACKLOG→{TODO}`、`TODO→{IN_PROGRESS,BACKLOG,DONE}`、`IN_PROGRESS→{IN_REVIEW,VERIFYING,TODO,DONE}`、`IN_REVIEW→{DONE,IN_PROGRESS}`、`VERIFYING→{DONE,IN_PROGRESS}`、`DONE→{IN_PROGRESS,TODO}`。前端 `statusTransitions` 与之严格一致，浮层仅渲染合法目标，避免非法迁移导致的 400。
