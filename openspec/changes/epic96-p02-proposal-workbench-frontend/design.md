# 设计：Proposal 问答工作台前端 UI

## 数据模型（对齐后端 `domains/proposals/models.py`）
- `ProposalItem`：`id` / `project_id` / `title` / `content` / `status` / `current_round` /
  `converged_spec` / `story_id` / `error` / `author_id` / `created_at` / `updated_at`。
- `ProposalRoundItem`：`id` / `proposal_id` / `round_no` / `summary` / `agent` / `questions[]`
  （含 `seq` / `question` / `answer` / `unsure` / `answered_at` / `answered_by`）。
- `ProposalQuestionItem`：单条问题；`isProposalQuestionAnswered(q)` 判定口径与后端 `answered_at`
  一致（`answered_at` 非空 / `unsure` / 有答案 任一即视为已处理）。

## 视图路由（假路由沿用 App 组件 `loadRoute`）
- `proposals` → `view='proposals'` → `loadProposals()`。
- `proposals/:id` → `view='proposal'` → `loadProposalDetail(id)`。
- 侧栏新增 `{ id:'proposals', title:'需求提案', ... }` 导航项（auth guard 与既有一致）。

## 状态管理（App 组件 signals）
- `proposals`（列表）、`proposalItem`（详情）、`proposalRounds`（按轮次问答）。
- `proposalDrafts`（Record<qid,string> 草稿答案）、`proposalUnsure`（Record<qid,boolean>）。
- `proposalSaving`（Set<qid> 单条保存中）、`proposalSubmitting`（本轮提交中）。
- `proposalFilterStatus` / `proposalSearchQuery`（列表本地即时过滤）。

## 交互流程
1. **列表**：`loadProposals()` 拉取；`@for` 渲染卡片（标题 / 项目 / 轮次 / 时间 / 状态徽标）；
   `#proposal-status-filter` 下拉 + `#proposal-search` 输入框做本地过滤；空态引导新建。
2. **新建**：`openProposalModal()` 先 `invalidateProjectCache()` + `loadProjects()` 刷新项目下拉
   （避免模态框打开时缺选项——实测 REST 新建项目后 SPA 列表缓存未包含），再打开弹窗；
   `submitProposalCreate()` 调 `createProposal` 并跳转工作台。
3. **派发**：`#proposal-queue-btn`（draft→queued）调 `setProposalStatus`。
4. **作答**：`textarea[data-answer-for]` 经 `(input)` → `setProposalDraft`；`input[data-unsure-for]`
   `(change)` → `toggleProposalUnsure`（勾选后禁用答案输入框）。
5. **单条保存**：`saveProposalAnswer(q)` 调 `answerProposalQuestion`，结束后 `loadProposalDetail` 回读。
6. **一键提交本轮**：`submitProposalRound()` 串行提交本轮所有「已填草稿或标记不确定」的未答问题
   （串行避免并发状态竞态），末条提交后后端自动 `awaiting→answered`；回读详情后
   `#proposal-all-done` 显示。

## 后端性能修复（本变更附带）
E2E 初测发现逐条作答串行提交时整轮耗时 ~15s、UI 卡在「提交中…」。根因：**审计日志中间件在
async 中间件内同步 `with SessionLocal()` 写库，阻塞 asyncio 事件循环**，串行请求累积成秒级延迟
（注释自称「异步非阻塞」但实际阻塞）。修复：
- `agentboard/api.py`：`audit_log_middleware` 将审计落库改为 `await asyncio.to_thread(_write_audit_log, ...)`
  （`_write_audit_log` 为同步写库 helper），不再阻塞事件循环。
- `agentboard/database.py`：SQLite 连接监听增加 `PRAGMA synchronous=NORMAL`（仅 sqlite URL 生效，
  不影响生产 MariaDB），消除 Windows 上多提交累积的 fsync 延迟。

## 兼容性
- 纯前端增量 + 后端非契约性性能修复；REST 契约 / 模型 / 迁移零改动。
- 审计中间件行为不变（仍记录同口径审计日志），仅写入时机移到线程池。
