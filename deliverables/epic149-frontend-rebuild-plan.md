# Epic 149 · 前端布局重建落库清单

> **项目**：AgentBoard (AGB, id=3)　**创建方式**：MCP　**创建时间**：2026-08-19
> **生产地址**：http://124.220.44.12/ （登录后进入项目 AGB 查看 Epic 149）

## Epic 149 — 前端布局重建：基于 Home & Workspace 原型 v7 重构

基于 commit `60e9cf1` 原型，采用「原型=设计契约 + 现有 Angular=载体」合并路径，分 5 阶段演进，indigo→navy 分阶段替换勿硬切。

| 阶段 | Story ID | 标题 | dev Task | qa Task |
| --- | --- | --- | --- | --- |
| 0 | **316** | 冻结设计契约：原型修 P1 + 令牌映射表 + MIGRATION.md | 1280 | 1281 |
| 1 | **317** | 外壳先行：令牌合并 + 两级 Shell 替换 topbar/tab | 1282 | 1283 |
| 2 | **318** | 侧边栏 + managed-list 抽象：projectSidebar + ManagedListComponent | 1284 | 1285 |
| 3 | **319** | 视图逐个迁移：8 视图从 @switch 拆独立组件 | 1286 | 1287 |
| 4 | **320** | 色板收口：indigo→navy 统一 + 暗色主题 | 1288 | 1289 |

## 依赖关系
```
316 (冻结契约) → 317 (外壳先行) → 318 (侧边栏+列表抽象) → 319 (视图逐个迁移) → 320 (色板收口)
```
阶段 0-1 可较快产出可见成果（外壳立刻变原型样子）；阶段 2-3 是核心重构（单体拆解）；阶段 4 是收口。

## Task 明细

| Task ID | Story | 类型 | 标题 | 优先级 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1280 | 316 | dev | 开发：原型修 P1 + MIGRATION.md + 令牌映射表 | high | todo |
| 1281 | 316 | qa | 测试：验证 P1 修复 + MIGRATION.md 完整性 + 令牌映射表 | medium | todo |
| 1282 | 317 | dev | 开发：令牌合并 + 两级 Shell 替换 topbar/tab | high | todo |
| 1283 | 317 | qa | 测试：验证外壳渲染 + 现有视图兼容 + SVG 图标替换 | medium | todo |
| 1284 | 318 | dev | 开发：projectSidebar 重构 + ManagedListComponent 抽取 | high | todo |
| 1285 | 318 | qa | 测试：验证 5 列表交互无回归 + 侧边栏导航 | medium | todo |
| 1286 | 319 | dev | 开发：8 视图从 @switch 拆独立组件 | high | todo |
| 1287 | 319 | qa | 测试：逐视图 E2E 验证（不整页刷新）+ 业务逻辑回归 | medium | todo |
| 1288 | 320 | dev | 开发：indigo 转 navy 统一 + 暗色主题令牌 | medium | todo |
| 1289 | 320 | qa | 测试：暗色主题对比度验证 + 旧令牌清理回归 | medium | todo |

## 配置说明
- 所有 Story `needs_design=false`（实现型，原型设计已冻结，走快速流 todo→in_progress，不过设计评审段）
- dev task 优先级 high（阶段 0-3 核心实现），qa task 优先级 medium
- 所有 task `assignment_mode=claim`（认领制），当前未指派

## 落地路径回顾
1. **原型不重做**：修 P1 后冻结为 v7 设计契约
2. **前端不重做**：保留现有 Angular（app.ts 7834 行业务逻辑），按原型重构视图层
3. **合并**：原型提供目标形态 + 设计令牌，现有 Angular 提供业务逻辑
4. **分阶段**：每阶段有可见产出、风险可控，indigo→navy 勿硬切

审查报告见 `deliverables/home-workspace-prototype-review.md`。
