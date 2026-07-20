# 变更提案：任务列表优先级快速筛选 chips（Epic 31 v2.0）

## 背景
AgentBoard 任务列表已具备搜索、排序、分组、折叠及「高级筛选面板」（优先级多选藏在 ⚙ 筛选 里）。
但 Jira 类工具的核心体验之一，是**常驻、可一键切换的优先级快速筛选条**，让用户无需展开面板即可按优先级收敛列表并直观看到各优先级任务数量。

## 目标
在 Story 任务列表工具条新增**常驻的优先级快速筛选 chips**：
- 全部 / 最高 / 高 / 中 / 低 / 最低，单选（再次点击同优先级取消）；
- 每个 chip 显示该优先级当前任务计数；
- 选中状态持久化到 `localStorage`（key `agentboard_quick_priority`），刷新后保留；
- 与「高级筛选面板」共享 `filterPriorities()` 状态，表现一致。

## 非目标
- 不改动后端契约 / `models.py` / `api.py` / `mcp_server.py`。
- 不改动既有排序、分组、搜索逻辑。
- 不做状态（status）维度的快速筛选（沿用既有高级面板）。

## 范围
- 纯前端（Angular SPA）：`frontend/src/app/app.ts` + `app.html` + `app.css`。
- 复用既有 `filterPriorities()` 信号（已接入 `visibleTasks` 过滤）。
- 新增 `priorityCounts` computed 统计各优先级数量。
- 新增 `setQuickPriority(p)` 单选切换 + `persistQuickPriority()` 持久化。

## 约束
- 新增前端代码 < 80 行（符合前端持续优化长期轨道纪律）。
- 不引入新框架 / 依赖。

## 影响
- 仅前端静态产物；无后端、无迁移、无 API 变更。
- `localStorage` 新增 key `agentboard_quick_priority`（数组，如 `["high"]`）。

## 退出标准
- 工具条出现优先级 chips，点击按优先级筛选任务，计数正确。
- 刷新页面后筛选选择保留。
- 与高级筛选面板状态同步（点高级面板优先级，工具条对应 chip 高亮）。
- Playwright E2E 全绿（0 page / console / 404 错误）。
