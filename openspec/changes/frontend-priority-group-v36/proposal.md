# Proposal: 任务列表分组新增「按优先级」维度 (Epic 49 v3.6)

## 背景
任务列表分组（Task 836）已支持：不分组 / 按状态 / 按类型 / 按负责人，并具备折叠持久化（v1.8/v1.9）。
优先级是任务最核心的排定维度之一，且与既有的「优先级快速筛选 chips（v2.0）」「批量改优先级（v2.9）」形成一致的信息体系。当前分组维度缺少「按优先级」，用户无法按优先级聚合查看任务分布。

## 目标
在分组下拉中新增「按优先级」选项，将任务按 `priority` 聚合为 highest/high/medium/low/lowest 分组，分组顺序遵循优先级工作流（高→低），分组头展示带色徽章与计数，与现有 `priority--*` 样式体系一致。

## 非目标
- 不改变后端数据模型或 API 契约（纯前端计算）。
- 不新增分组维度的持久化（复用既有 `localStorage.agentboard_story_group`）。
- 不改动既有状态/类型/负责人分组的逻辑。

## 影响范围
- `frontend/src/app/app.ts`：`taskGroupBy` 类型与 `taskGroupOptions`、分组分桶键序、`groupLabel` 文案。
- `frontend/src/app/app.html`：分组头新增优先级色徽章。
- 无后端改动；构建产物 `agentboard/web/static/` 同步更新（web 8080/docker 28080 即时生效）。
