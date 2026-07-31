# 变更提案：Proposal 问答工作台前端 UI（Epic 96 P0-2）

## 背景
Epic 96 P0 的 Story #154 要求「Proposal 实体/状态机/前端问答工作台」整体交付。P0-1（Task #922）
已落地后端三表 + 状态机 + REST 契约（`epic96-p0-proposal-backend`），但**前端问答工作台 UI 完全缺失**——
Web 端无法创建提案、无法逐条作答、无法提交澄清答案。Story #154 因此长期处于半成品状态，
人机澄清闭环在 Web 端不可用。

本变更补齐 P0 的**前端部分**，使 Proposal 澄清回路在 Web 端可完整操作：
创建提案 → 查看轮次问题 → 逐条作答 / 标记不确定 → 一键提交 → 状态推进。

## 目标
纯前端增量实现，零 REST 契约变更：
1. `proposals` 列表视图（标题 / 状态徽标 / 轮次 / 更新时间 / 状态筛选 / 关键词搜索 / 空态）。
2. 新建提案弹窗（title + content + project 选择）。
3. Proposal 详情工作台：左栏需求正文 + 状态时间线，右栏按 round 分组的问题卡片。
4. 问题卡片：输入答案、标记「暂不确定」(unsure)、单条保存、批量一键提交本轮。
5. 状态徽标覆盖 `draft/queued/analyzing/awaiting/answered/converged/story_created/failed` 全枚举，
   暗色主题适配。
6. 状态推进：手动 `draft→queued`、失败重试 `failed→queued`。

## 非目标
- 不改动后端任何 REST 契约 / 模型 / 迁移（沿用 P0-1 契约）。
- 不实现 MQ 派发 / Worker 消费者（P1）。
- 不实现 proposal→story 自动生成（P3）。
- 不触碰端口 18001（MCP）或任何 docker 端口配置。

## 范围
- `frontend/src/app/models.ts`：新增 `ProposalStatus` / `PROPOSAL_STATUSES` / `ProposalItem` /
  `ProposalRoundItem` / `ProposalQuestionItem` 类型（对齐 `domains/proposals/models.py`）。
- `frontend/src/app/api.service.ts`：新增 `listProposals` / `getProposal` / `createProposal` /
  `updateProposal` / `setProposalStatus` / `deleteProposal` / `listProposalRounds` /
  `answerProposalQuestion`（均失效 `/api/proposals` 缓存前缀）。
- `frontend/src/app/app.routes.ts`：新增 `proposals` / `proposals/:id` 路由。
- `frontend/src/app/app.ts`：导航项 + `ViewKind` 扩展 + 提案信号（`proposals` / `proposalItem` /
  `proposalRounds` / 草稿 / 不确定 / 提交中态）+ 方法（`loadProposals` / `loadProposalDetail` /
  `saveProposalAnswer` / `submitProposalRound` / `openProposalModal` 等）。
- `frontend/src/app/app.html`：侧栏入口 + 列表 `@case('proposals')` + 工作台 `@case('proposal')`
  （双栏 + 问题卡片 + 新建弹窗）。
- `frontend/src/app/app.css`：提案徽标 / 工作台 / 问题卡片 / 时间线样式（含暗色）。
- `tests/test_epic96_p02_proposal_workbench_e2e.py`：Playwright 真实浏览器 E2E（创建 → 派发 →
  模拟 Agent 回写问题 → 逐条作答 + 不确定 → 一键提交 → 断言全部已答 / 状态 answered → 0 报错）。

## 影响
- 仅新增前端文件内容 + 新增路由 / 视图；不改动任何既有端点契约、后端模型。
- 新增页面：`/proposals`（列表）、`/proposals/:id`（工作台）。
- 侧栏新增「需求提案」入口。
- 复用既有项目成员作用域：提案列表 / 详情经既有 `api:read` 鉴权拉取。

## 退出标准
- Playwright E2E：登录 → 打开工作台 → 真实 REST 造 analyzing + 3 问题 → 页面渲染问题卡片 →
  作答 2 条 + 标记 1 条不确定 → 一键提交 → 断言 3 条全已答、状态推进 `answered` → 全程 0 控制台
  报错 / 0 JS 异常 / 无 404。
- 状态筛选 + 关键词搜索 + 暗色主题下工作台可读。
- 回归：既有 pytest 套件与既有 E2E 无新增失败（后端 audit 中间件改为线程池写入亦无回归）。
- 不得修改 REST 契约；不得触碰端口 18001。
