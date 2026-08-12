# Proposal: 命令面板接入 AgentRun 后端搜索（v6.20）

## 背景

命令面板（Ctrl/Cmd+K，Epic 67 v5.4）实体后端搜索体系自 v5.6 起逐步补齐：任务（`/api/tasks?q=`）、项目（本地过滤）、Story（`/api/search/stories`）、文档（`/api/documents?q=`）、Epic（`/api/search/epics`，v6.13）、Sprint（`/api/search/sprints`，v6.14）、通知（`/api/search/notifications`，v6.15）、Agent（`/api/search/agents`，v6.16）、Proposal（`/api/search/proposals`，v6.17）、Ticket（`/api/search/tickets`，v6.18）、Schedule（`/api/search/schedules`，v6.19）。**执行记录（AgentRun，Epic 78 执行器闭环产物）实体缺失**——AgentRun 记录每次 Agent 调度的运行结果（success/failed/pending/running），是自动化开发闭环的可观测性核心，目前只能在项目视图「定时计划」Tab 的运行历史中看到，命令面板无法直接搜索（状态/摘要/错误信息）并跳转，体验与其余十一类不一致。

## 目标

1. 后端新增全局执行记录关键词搜索端点 `GET /api/search/runs`。`AgentRun` 无 `project_id` 列，通过 **join `AgentSchedule`** 取得归属项目；匹配 `status` / `summary` / `error_message`；**可见性收敛镜像 `search_schedules`**（普通用户仅搜索自己 ProjectMember 项目下的执行记录，admin 全量；带鉴权）。返回 `_ser(run)` 全列 + 附加 `project_id`。
2. 前端命令面板补齐第 12 类实体结果：`paletteRunResults` 信号 + `paletteRunSearch` 分支 + `paletteItems` 合并 + `.cat-run` 分类标签 + 点击跳转 `/project/{project_id}/schedules`（项目定时计划 Tab 运行历史区）。
3. 纯增量：零既有 REST/DB 契约破坏、零新增依赖。

## 非目标

- 不做 WebhookConfig / Attachment / AuditLog 等其余实体搜索（后续按需补充）。
- 不改变命令面板既有交互（Ctrl+K、↑↓、Enter、Esc）。
- 不改项目定时计划 Tab 本身（仅提供跳转入口）。

## 成功指标

- pytest 单测覆盖 service 与端点（status/summary/error_message 匹配、join 附加 project_id、可见性收敛 admin/成员、limit、401 未鉴权、q 必填、limit 上限、路由不冲突、与既有搜索端点并存）。
- Playwright E2E：输入唯一 token → 出现 `.cat-run` 结果 → 点击进入项目 `schedules` Tab → 0 pageerror/console/js·css 404。
- vitest：paletteItems 合并 / `.cat-run` 标签渲染 / open-close 清空。

## 参考

- `docs/tasks.md` Epic 11 命令面板实体搜索体系
- `openspec/changes/palette-schedule-search-v619/`（v6.19 同类实施，可见性收敛 + 端点模板）
- `openspec/changes/palette-ticket-search-v618/`（v6.18 同类实施，join 附加 project_id 模板）
