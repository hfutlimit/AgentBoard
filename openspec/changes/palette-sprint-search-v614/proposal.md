# Proposal: 命令面板接入 Sprint 搜索（v6.14）

## 背景

命令面板（Ctrl/Cmd+K）实体搜索体系已有 5 类：任务（`/api/tasks?q=`）、项目（本地过滤）、Story（`/api/search/stories`）、文档（`/api/documents?q=`）、Epic（v6.13 `/api/search/epics`）。**Sprint 实体缺失**——用户无法在命令面板直接搜到 Sprint 并跳转项目 Sprint 看板，是实体搜索体系的最后一类缺口。

## 目标

1. 后端新增全局 Sprint 关键词搜索端点 `GET /api/search/sprints`（镜像 `search_epics`，匹配 `title/goal`，避免与 `/api/projects/{pid}/sprints` 路由冲突）。
2. 前端命令面板补齐第 6 类实体结果：`paletteSprintResults` 信号 + `paletteRunSearch` 分支 + `paletteItems` 合并 + `.cat-sprint` 分类标签，跳转 `/sprint/{id}`。
3. 纯增量：零既有 REST/DB 契约破坏、零新增依赖。

## 非目标

- 不做 Sprint 模糊搜索/排序权重调整。
- 不改变命令面板既有交互（Ctrl+K、↑↓、Enter、Esc）。

## 成功指标

- pytest 单测覆盖 service 与端点（title/goal 匹配、limit、路由不冲突、空结果）。
- Playwright E2E：输入唯一 token → 出现 `.cat-sprint` 结果 → 跳转 `/sprint/{id}` 渲染 → 0 pageerror/console/js·css 404。
