# Proposal: 命令面板接入 Epic 后端搜索（v6.13）

## 背景

命令面板（Ctrl/Cmd+K，Epic 67 v5.4）自 v5.6/v5.7 起已接入四类实体后端搜索：任务（`/api/tasks?q=`）、项目（本地过滤）、Story（`/api/search/stories`）、文档（`/api/documents?q=`）。**Epic 实体缺失**——用户在命令面板输入关键词时无法直接搜到 Epic 并跳转，需先进入项目再逐级翻找，与其余四类的"即输即达"体验不一致。

## 目标

1. 后端新增全局 Epic 关键词搜索端点 `GET /api/search/epics`（镜像 `search_stories`，避免与 `/api/epics/{eid}` 路由冲突）。
2. 前端命令面板补齐第 5 类实体结果：`paletteEpicResults` 信号 + `paletteRunSearch` 分支 + `paletteItems` 合并 + `.cat-epic` 分类标签。
3. 纯增量：零既有 REST/DB 契约破坏、零新增依赖。

## 非目标

- 不做 Epic 的模糊搜索/排序权重调整。
- 不改变命令面板既有交互（Ctrl+K、↑↓、Enter、Esc）。

## 成功指标

- pytest 单测覆盖 service 与端点（标题/描述匹配、limit、路由不冲突、与 story 端点并存）。
- Playwright E2E：输入唯一 token → 出现 `.cat-epic` 结果 → 点击跳转 `/epic/{id}` → 无匹配空态 → 0 pageerror/console/js·css 404。
