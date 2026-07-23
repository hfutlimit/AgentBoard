# Proposal: 批量状态变更状态机感知 (v3.5 / Epic 48)

## 问题
v3.4 已让任务行内状态切换遵循后端 `TRANSITIONS` 状态机（仅展示合法目标），但**批量状态面板**（`bulkActionTarget()==='status'`）仍遍历全部 6 个状态。用户选中多个不同状态的任务后，面板会展示所有状态，点选非法流转目标时后端 `set_status` 直接 400（造成部分成功 / 部分失败、体验割裂、与 v3.4 行为不一致）。

## 目标
将批量状态面板升级为**状态机感知**：
- 计算「所选任务」各自合法目标状态的**交集**，仅渲染交集内的状态按钮
- 交集为空时显示「无共同可流转目标（受状态机限制）」提示，隐藏按钮
- 复用 v3.4 的 `statusTransitions` 镜像与 `selectedTasks` 信号，纯前端实现，零后端契约变更

## 非目标
- 不改动后端状态机 / `set_task_status` 端点
- 不做「并集」展示（并集会让部分任务命中非法流转，仍会触发 400）
- 不引入新的批量操作类型

## 风险
低。仅新增一个 `computed`（交集计算）+ 模板分支（空态提示），复用既有 `statusTransitions` / `bulkUpdateStatus` / `statusLabel`。`computed` 在选中为空时返回 `[]`，面板不开则不渲染。

## 状态机对齐
`statusTransitions` 与 v3.4 完全一致（见 `app.ts:2990`）。批量交集示例：
- 选 `todo`+`todo`+`in_progress` → 交集 `{done}` → 仅「完成」
- 选 `backlog`+`todo` → 交集 `{}` → 空态提示
