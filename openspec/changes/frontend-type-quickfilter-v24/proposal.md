# 变更提案：前端体验升级 v2.4 — 任务类型快速筛选 chips

## 背景
AgentBoard 任务列表工具条已落地两类快速筛选 chips（v2.0 优先级、v2.5 状态），并支持分组、排序、搜索、只看我。但「按类型（任务 / Bug）」目前仅藏在高级筛选面板（多选自选），缺少与优先级/状态一致的**常驻单选 chips** 入口，难以一眼按 issue 类型收敛列表。

## 目标
在任务列表工具条新增第三组**类型快速筛选 chips**：
- 「全部」+「任务」+「Bug」三枚单选 chip，带实时计数。
- 单选切换（再点同类型取消），与优先级/状态 chips 交互一致。
- 选择持久化到 `localStorage.agentboard_quick_type`，reload 后保留。
- 复用既有 `filterTypes` 信号与 `visibleTasks()` 过滤逻辑，**不改动后端契约**。

## 非目标
- 不引入新框架 / 构建链。
- 不改动 `models.py` / `api.py` / 数据模型。
- 不改变高级筛选面板已有的 `toggleFilterType` 多选自选行为（两者写同一信号，并存）。

## 范围
- 仅 `frontend/src/app/app.ts`（信号 + 方法 + computed）与 `frontend/src/app/app.html`（工具条 chip 块）。
- `app.css` 复用既有 `.qf-chip` / `.qf-count`，无需新增样式类。

## 影响
- 仅前端静态产物（`frontend/dist` → `agentboard/web/static/`）。
- 不影响数据模型、API、MCP、迁移、docker 配置。

## 退出标准
- 类型 chips 渲染且计数正确；点「Bug」仅保留 bug 任务；reload 后持久化；点「全部」清空。
- Playwright E2E 全绿（0 pageerror / console / .js+.css 404）。
- 既有 E2E / pytest 回归无失败。
