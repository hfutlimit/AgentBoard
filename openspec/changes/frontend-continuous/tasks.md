# 任务清单：前端持续优化（frontend-continuous）

> 本变更为**长期容器**。详细 backlog 与勾选以 `docs/tasks.md` Epic 11 为权威源；此处仅记录流程与索引，避免重复维护。

## 迭代节奏
- 每个自动任务周期 = 一项小优化（A-xx / P-xx / 经评估的 B-xx）。
- 自动任务读取 `docs/tasks.md` Epic 11 的 backlog，取**第一个未勾选项**，按 `design.md` 的纪律实现、验证、勾选、commit。

## 索引
- 迭代规则与 backlog：见 `docs/tasks.md` → `## Epic 11：持续前端优化（模仿 Jira，小步迭代）`
- 设计纪律：见 `design.md`（范围红线 / 复用现有能力 / 后端依赖评估流程）
- **UI 风格重设计（本轮优先，P-01~P-15）**：
  - 完整设计提案：`docs/design/ui-style-proposal.md`
  - 逐任务规格（含行数/依赖/验收）：`docs/design/ui-style-tasks.md`
  - 高保真原型（含明/暗切换，浏览器直接打开）：`docs/design/mockup.html`
  - 当前页面截图证据（重设计前基线）：`docs/design/shots/`
- **候选首项**：**P-01 设计 Token 体系**（位于 Epic 11「Backlog C（UI 风格重设计）」，为当前第一个未勾选项）

## 状态
- [x] A-01 至 A-17 已交付（2026-07-10 至 2026-07-11）
- [x] A-13 任务详情抽屉已交付（2026-07-11），但新增代码合计约 114 行，超过范围红线，记为流程例外
- [ ] A-11 响应式、A-12 Toast、A-13 抽屉的真实浏览器回归纳入 `openspec/changes/playwright-e2e/`
- [ ] 待自动任务认领下一项：**P-01 设计 Token 体系**（UI 风格重设计 Backlog C 首项）
- [ ] A-18 / A-19 / A-20（Backlog A 残项）将在 P-01~P-15 全部完成后继续认领
