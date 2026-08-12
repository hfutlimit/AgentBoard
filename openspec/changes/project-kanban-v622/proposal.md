# Proposal: 项目级看板

**状态**: accepted
**日期**: 2026-08-12

## 需求

1. 项目级「看板」tab，一个项目一个看板，卡片=Story。
2. ticket（Story）「进入 kanban」标记，标记后 Agent 开始自动化处理。
3. 看板列复用 Story 状态流转；卡片展示 design/dev/qa task 状态。
4. 自动化流程：认领 → design → dev → review → qa → 完成（事件驱动 + worker 编排）。
5. 并发控制：每项目上限 5（本迭代沿用 CAS + prefetch，不引入独立配额）。

## 决策

- `stories.in_kanban` 布尔标记 + `GET /api/projects/{pid}/kanban` 端点；
- 标记进看板自动 confirm（backlog→confirmed）+ 广播 `story.confirmed` / `task.available`，触发既有 worker story 编排（design→dev→review→qa 已由 StoryHandler 承担）；
- 看板列序固定：backlog→confirmed→todo→in_progress→in_review→verifying→done→blocked；
- 前端独立 tab + 卡片三态徽章。

## 影响

新增迁移 + 增量 API + 前端 tab；不破坏既有 Story 状态机契约。
