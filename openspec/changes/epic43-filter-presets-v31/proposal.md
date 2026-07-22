# 变更提案：任务列表筛选预设（Epic 43 v3.1）

## 背景
AgentBoard 任务列表已完成快速筛选 chips 家族（优先级 / 状态 / 类型 / 指派人 / 截止日期）、搜索、排序、分组、折叠、批量操作等体验优化（v1.5–v3.0）。
但在日常使用中，用户常需要**反复套用同一组筛选条件**（例如「指派人=我 + 状态=进行中 + 类型=Bug」）。当前每次都要手动重新点击多个 chip，效率低。

Jira / Linear 等工具普遍提供**已保存的视图 / 筛选预设**，让用户一键复用常用组合。本变更补齐该能力。

## 目标
在 Story 任务列表工具条新增**筛选预设（Filter Presets）**能力（纯前端，`localStorage` 持久化）：
- 工具条新增「📑 预设」按钮，显示已保存数量徽标；
- 点击展开面板，可输入名称并「保存当前」筛选组合（5 维度 chips + 搜索 + 只看我）；
- 面板列出已保存预设，点击名称即**应用**（还原全部筛选条件），点 ✕ 删除；
- 预设持久化到 `localStorage`（key `agentboard_filter_presets`），刷新后保留；
- 与既有 `filterStatus / filterPriorities / filterTypes / filterAssignees / filterDueDate / taskSearchQuery / filterMineOnly` 信号共享状态，表现一致。

## 非目标
- 不改动后端契约 / `models.py` / `api.py` / `mcp_server.py`（无任何 HTTP 接口变更）。
- 不改动既有 chips / 搜索 / 排序 / 分组 / 批量逻辑。
- 不做服务端共享预设（仅为本地个人偏好）。

## 范围
- 纯前端（Angular SPA）：`frontend/src/app/app.ts` + `app.html` + `app.css`。
- 新增 `FilterPreset` 接口与 `filterPresets / presetName / presetOpen` 信号。
- 新增 `saveFilterPreset() / applyFilterPreset(idx) / deleteFilterPreset(idx) / togglePresetOpen()` 方法。
- 复用既有 `clearAllFilters()` + `setQuick*()` + `taskSearchQuery.set()` 还原筛选状态。

## 约束
- 新增前端代码 < 80 行（符合前端持续优化长期轨道纪律）。
- 不引入新框架 / 依赖。

## 影响
- 仅前端静态产物；无后端、无迁移、无 API 变更；无回归风险（独立新功能）。
