# 任务清单：Proposal 问答工作台前端 UI（Epic 96 P0-2 · Task 930）

## Task 930 — P0-2 · Proposal 问答工作台前端 UI
- [x] `frontend/src/app/models.ts`：新增 `ProposalStatus` 联合类型 / `PROPOSAL_STATUSES` 数组 /
      `ProposalItem` / `ProposalRoundItem` / `ProposalQuestionItem` 接口（对齐后端模型）。
- [x] `frontend/src/app/api.service.ts`：新增 `listProposals` / `getProposal` / `createProposal` /
      `updateProposal` / `setProposalStatus` / `deleteProposal` / `listProposalRounds` /
      `answerProposalQuestion`，各方法失效 `/api/proposals` 缓存前缀。
- [x] `frontend/src/app/app.routes.ts`：新增 `proposals` / `proposals/:id` 路由（RouteAnchor）。
- [x] `frontend/src/app/app.ts`：
  - `ViewKind` 扩展 `proposals | proposal`；导入提案类型。
  - 信号：`proposals` / `proposalItem` / `proposalRounds` / `proposalFilterStatus` /
    `proposalSearchQuery` / `proposalDrafts` / `proposalUnsure` / `proposalSaving` /
    `proposalSubmitting` / 新建弹窗相关。
  - 方法：`proposalStatusLabel` / `proposalVisible` / `loadProposals` / `onProposalFilterChange` /
    `loadProposalDetail` / `syncProposalDrafts` / `proposalDraftOf` / `setProposalDraft` /
    `proposalUnsureOf` / `toggleProposalUnsure` / `isProposalQuestionAnswered` /
    `proposalPendingCount` / `currentProposalRound` / `saveProposalAnswer` / `submitProposalRound` /
    `openProposalModal`（刷新项目缓存）/ `closeProposalModal` / `submitProposalCreate` /
    `advanceProposalStatus`。
  - `loadRoute` 增加 `proposals` / `proposal` 分支；侧栏新增「需求提案」导航项。
- [x] `frontend/src/app/app.html`：侧栏入口 `#nav-proposals`；`@case('proposals')` 列表（空态 /
      卡片 / 状态筛选 / 搜索 / 新建按钮）；`@case('proposal')` 工作台（左栏正文 + 时间线 + 收敛规格，
      右栏轮次分组问题卡片 + 单条保存 + 一键提交 + 全部完成态；新建提案弹窗）。
- [x] `frontend/src/app/app.css`：`.proposal-intro` / `.proposal-list` / `.proposal-row` /
      `.badge.pstatus--{...}` 全枚举（含 `.dark`）/ `.proposal-workbench`（双栏栅格，<1100px 折叠）/
      `.proposal-pane` / `.proposal-content` / `.proposal-error` / `.proposal-timeline` /
      `.proposal-round` / `.proposal-question` / `.proposal-answer-input` / `.proposal-unsure` /
      `.proposal-all-done` 等。
- [x] `agentboard/api.py`：审计日志中间件改为 `await asyncio.to_thread(_write_audit_log, ...)`，
      消除 async 中间件内同步写库对事件循环的阻塞（附带修复，使串行作答类请求不再卡顿）。
- [x] `agentboard/database.py`：SQLite 连接监听增加 `PRAGMA synchronous=NORMAL`（仅 sqlite 生效）。
- [x] `tests/test_epic96_p02_proposal_workbench_e2e.py`：Playwright 真实浏览器 E2E（2 用例，全绿）。

## 验证结论
- `pytest tests/test_epic96_p02_proposal_workbench_e2e.py`：**2 passed（21.6s）**——
  用例 1 覆盖创建→派发→模拟 Agent 回写 3 问题→逐条作答 2 + 标记不确定 1→一键提交→
  断言 3 条全已答、状态 `answered`、服务端真值校验、列表徽标/轮次、0 控制台/页面报错；
  用例 2 覆盖状态筛选 + 关键词搜索 + 暗色主题工作台可读。截图 `screenshots/epic96_p02_workbench_*.png`。
- 回归：`pytest tests/test_epic30_cache.py`（独立 7 passed / 1 skipped）、
  `tests/test_epic96_p0_proposals.py`、`tests/test_epic96_p0_proposals_e2e.py` 全绿；
  audit 中间件改为线程池写入后既有鉴权/审计行为不变。
- MCP 状态流转：Task #930、Story #154、Epic #96 均推进至 `in_review`。
- 兼容性：零 REST 契约变更；未触碰端口 18001 / docker 配置；`init_db()` 启动自动迁移，无需手动步骤。

## 后续（P1+，本变更不涵盖）
- MCP 4 工具 + Worker 消费者 + 无头 WorkBuddy 调用（先 DB 轮询）。
- proposal→story / task 自动生成（P3）。
