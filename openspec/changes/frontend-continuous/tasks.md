# 任务清单：前端持续优化（frontend-continuous）

> 本变更为**长期容器**。详细 backlog 与勾选以 `docs/tasks.md` Epic 11 为权威源；此处仅记录流程与索引，避免重复维护。

## 迭代节奏
- 每个自动任务周期 = 一项小优化（A-xx）。
- 自动任务读取 `docs/tasks.md` Epic 11 的 backlog，取**第一个未勾选项**，按 `design.md` 的纪律实现、验证、勾选、commit。

## 索引
- 迭代规则与 backlog：见 `docs/tasks.md` → `## Epic 11：持续前端优化（模仿 Jira，小步迭代）`
- 设计纪律：见 `design.md`（范围红线 / 复用现有能力 / 后端依赖评估流程）
- 候选首项：**A-01 看板视图（Story 页）**

## 状态
- [x] A-01 至 A-12 已按 `docs/tasks.md` 交付（2026-07-10 至 2026-07-11）
- [x] A-13 任务详情抽屉已交付（2026-07-11），但新增代码合计约 114 行，超过范围红线，记为流程例外
- [ ] A-11 响应式、A-12 Toast、A-13 抽屉的真实浏览器回归纳入 `openspec/changes/playwright-e2e/`
- [ ] 待自动任务认领下一项：A-14 Markdown 编辑工具栏
