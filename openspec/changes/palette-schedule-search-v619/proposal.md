# Proposal: 命令面板接入 Schedule 后端搜索（v6.19）

## 背景

命令面板（Ctrl/Cmd+K，Epic 67 v5.4）实体后端搜索体系自 v5.6 起逐步补齐：任务（`/api/tasks?q=`）、项目（本地过滤）、Story（`/api/search/stories`）、文档（`/api/documents?q=`）、Epic（`/api/search/epics`，v6.13）、Sprint（`/api/search/sprints`，v6.14）、通知（`/api/search/notifications`，v6.15）、Agent（`/api/search/agents`，v6.16）、Proposal（`/api/search/proposals`，v6.17）、Ticket（`/api/search/tickets`，v6.18）。**定时计划（AgentSchedule，AgentRun 执行器 / AgentSchedule 调度管理）实体缺失**——计划是自动化开发闭环（Epic 78 执行器）的调度核心，目前只能在项目视图「定时计划」Tab 看到，命令面板无法直接搜索计划（标题/绑定 Agent/类型）并跳转，体验与其余十类不一致。

## 目标

1. 后端新增全局定时计划关键词搜索端点 `GET /api/search/schedules`（匹配 `title` / `agent` / `schedule_type`，**可见性收敛镜像 `search_proposals`**：普通用户仅搜索自己 ProjectMember 项目下的计划，admin 全量；带鉴权）。返回 `_ser(AgentSchedule)` 全列（`AgentSchedule` 自带 `project_id`，无需反查）。
2. 前端命令面板补齐第 11 类实体结果：`paletteScheduleResults` 信号 + `paletteRunSearch` 分支 + `paletteItems` 合并 + `.cat-schedule` 分类标签 + 点击跳转 `/project/{project_id}/schedules`（项目定时计划 Tab）。
3. 纯增量：零既有 REST/DB 契约破坏、零新增依赖。

## 非目标

- 不做 AgentRun（运行记录）搜索——运行记录属于执行历史，后续可单独补充。
- 不改变命令面板既有交互（Ctrl+K、↑↓、Enter、Esc）。
- 不改项目定时计划 Tab 本身（仅提供跳转入口）。

## 成功指标

- pytest 单测覆盖 service 与端点（title/agent/schedule_type 匹配、可见性收敛 admin/成员、limit、401 未鉴权、q 必填、limit 上限、路由不冲突、与既有搜索端点并存）。
- Playwright E2E：输入唯一 token → 出现 `.cat-schedule` 结果 → 点击进入项目 `schedules` Tab → 0 pageerror/console/js·css 404。
- vitest：paletteItems 合并 / `.cat-schedule` 标签渲染 / open-close 清空。

## 参考

- `docs/tasks.md` Epic 11 命令面板实体搜索体系
- `openspec/changes/palette-ticket-search-v618/`（v6.18 同类实施，可见性收敛 + 端点模板）
- `openspec/changes/palette-proposal-search-v617/`（v6.17 同类实施）
