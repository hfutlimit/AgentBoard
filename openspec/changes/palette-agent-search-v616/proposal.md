# Proposal: 命令面板接入 Agent 后端搜索（v6.16）

## 背景

命令面板（Ctrl/Cmd+K，Epic 67 v5.4）实体后端搜索体系自 v5.6 起逐步补齐：任务（`/api/tasks?q=`）、项目（本地过滤）、Story（`/api/search/stories`）、文档（`/api/documents?q=`）、Epic（`/api/search/epics`，v6.13）、Sprint（`/api/search/sprints`，v6.14）、通知（`/api/search/notifications`，v6.15）。**Agent 实体缺失**——Agent 注册表（Epic 122 S1）已在侧栏 Agent 池视图展示，但命令面板无法直接搜索 Agent 并跳转，体验与其余七类不一致。

## 目标

1. 后端新增全局 Agent 关键词搜索端点 `GET /api/search/agents`（镜像 `search_sprints`/`search_notifications`，匹配 agent_id/name/roles，仅返回 enabled，带鉴权）。
2. 前端命令面板补齐第 8 类实体结果：`paletteAgentResults` 信号 + `paletteRunSearch` 分支 + `paletteItems` 合并 + `.cat-agent` 分类标签 + 点击跳转 Agent 池视图。
3. 纯增量：零既有 REST/DB 契约破坏、零新增依赖。

## 非目标

- 不做 Agent 的模糊搜索/排序权重调整。
- 不改变命令面板既有交互（Ctrl+K、↑↓、Enter、Esc）。
- 不改 Agent 池视图本身（仅提供跳转入口）。

## 成功指标

- pytest 单测覆盖 service 与端点（agent_id/name/roles 匹配、enabled 过滤、limit、401 未鉴权、q 必填、路由不冲突、与既有搜索端点并存）。
- vitest 覆盖信号合并、分类标签渲染、open/close 重置。
- Playwright E2E：输入唯一 token → 出现 `.cat-agent` 结果 → 点击进入 Agent 池视图 → 0 pageerror/console/js·css 404。

## 参考

- `docs/tasks.md` Epic 11 命令面板实体搜索体系
- `openspec/changes/palette-epic-search-v613/`（v6.13 同类实施）
- `openspec/changes/frontend-palette-story-doc-v57/`（v5.7 同类实施）
