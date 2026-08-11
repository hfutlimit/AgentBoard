# Proposal: 命令面板接入 Ticket 后端搜索（v6.18）

## 背景

命令面板（Ctrl/Cmd+K，Epic 67 v5.4）实体后端搜索体系自 v5.6 起逐步补齐：任务（`/api/tasks?q=`）、项目（本地过滤）、Story（`/api/search/stories`）、文档（`/api/documents?q=`）、Epic（`/api/search/epics`，v6.13）、Sprint（`/api/search/sprints`，v6.14）、通知（`/api/search/notifications`，v6.15）、Agent（`/api/search/agents`，v6.16）、Proposal（`/api/search/proposals`，v6.17）。**Ticket（Proposal → Ticket 异步转化请求，文档 #59）实体缺失**——工单是 Proposal 闭环（Epic 96/123/130）的核心产物，目前只能在提案详情页看到，命令面板无法直接搜索工单（标题/类型/状态）并跳转提案详情，体验与其余九类不一致。

## 目标

1. 后端新增全局 Ticket 关键词搜索端点 `GET /api/search/tickets`（匹配 `title` / `type` / 关联提案 `title`，**可见性收敛镜像 `search_proposals`**：普通用户仅搜索自己 ProjectMember 项目下提案关联的工单，admin 全量；带鉴权）。返回记录在 `_ser(ProposalTicketRequest)` 基础上附加 `project_id`（经提案反查），供前端显示项目名。
2. 前端命令面板补齐第 10 类实体结果：`paletteTicketResults` 信号 + `paletteRunSearch` 分支 + `paletteItems` 合并 + `.cat-ticket` 分类标签 + 点击跳转 `/proposals/{proposal_id}` 详情（工单区）。
3. 纯增量：零既有 REST/DB 契约破坏、零新增依赖。

## 非目标

- 不做 Ticket 的模糊搜索/排序权重调整。
- 不改变命令面板既有交互（Ctrl+K、↑↓、Enter、Esc）。
- 不改提案详情页本身（仅提供跳转入口）。

## 成功指标

- pytest 单测覆盖 service 与端点（title/type/提案标题匹配、可见性收敛 admin/成员、project_id 附加字段、limit、401 未鉴权、q 必填、limit 上限、路由不冲突、与既有搜索端点并存）。
- Playwright E2E：输入唯一 token → 出现 `.cat-ticket` 结果 → 点击进入 `/proposals/{id}` 详情 → 0 pageerror/console/js·css 404。

## 参考

- `docs/tasks.md` Epic 11 命令面板实体搜索体系
- `openspec/changes/palette-proposal-search-v617/`（v6.17 同类实施，可见性收敛模式）
- `openspec/changes/palette-agent-search-v616/`（v6.16 同类实施）
