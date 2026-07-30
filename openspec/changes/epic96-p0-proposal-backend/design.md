# 设计：Proposal 后端基座

## 领域模型（三张独立表，不复用 Task）
- `proposals`：`project_id`(FK CASCADE)、`title`、`content`、`status`(索引)、`current_round`(默认 0)、
  `converged_spec`、`story_id`(FK SET NULL)、`author_id`(FK)、`error`、时间戳。
- `proposal_rounds`：`proposal_id`(FK CASCADE)、`round_no`、`summary`、`agent`、时间戳；
  `UniqueConstraint("proposal_id","round_no")` → 重投复用既有 round。
- `proposal_questions`：`proposal_id`(FK)、`round_id`(FK)、`seq`、`question`、`answer`、
  `unsure`(Boolean)、`answered_at`、`answered_by`(FK)。

所有表建在共享 `Base`（`agentboard/domains/proposals/models.py`），由 `agentboard/models.py`
facade 统一导出，确保 Alembic metadata 与既有表注册在同一引擎。

## 状态机（PROPOSAL_TRANSITIONS）
```
draft → queued → analyzing → awaiting → answered → converged → story_created
                                      ↘ (reject) analyzing
failed → queued / draft
```
- `ASKABLE_STATUSES = {ANALYZING}`：仅 analyzing 状态可追加提问。
- 非法迁移抛 `IllegalTransition`（API 层转 400）。
- `set_proposal_status` 接受 `error` 参数；非 failed 迁移时清空 `error` 字段。

## 服务层（agentboard/service.py）
- `create_proposal`：初始 `draft`，按项目成员作用域创建。
- `list_proposals`：按 `project_id` 成员作用域过滤（与管理文档一致），支持 status 过滤。
- `set_proposal_status`：`_check_proposal_status` 校验迁移合法性。
- `create_proposal_round`：幂等——若 `(proposal_id, round_no)` 已存在则复用，不重复插入
  （at-least-once 兜底）。
- `add_proposal_questions`：仅 analyzing 可调用；剥离空问题；写入后自动把 proposal 推进到
  `awaiting`；`seq` 顺序自增。
- `answer_proposal_question`：支持 `unsure` 标记；写 `answered_at` / `answered_by`。
- `_maybe_mark_answered`：当某 round 所有问题均作答后，自动 `awaiting → answered`。
- `delete_proposal`：显式级联删除 rounds / questions（避免孤儿行）。

## REST API（agentboard/api.py）
- 新增 Pydantic：`ProposalIn` / `ProposalPatch` / `ProposalStatusIn` / `ProposalAskIn` /
  `ProposalAnswerIn`。
- 端点见 proposal.md「影响」一节。
- 访问控制：`require_business_auth` 中间件已对 `/api/proposals/(\d+)` 解析 project_id 并做
  成员校验；审计日志 entity 映射补充 `proposal` / `proposal_question`。
- `GET /api/proposals/pending`：供 Worker 轮询 `queued` 状态 proposal（无头 WorkBuddy 消费）。

## 迁移（h4i5j6k7l8m9_add_proposals.py）
- `down_revision = "g3h4i5j6k7l8"`，`upgrade()` 建三表：状态列加 CHECK 约束（SQL 字面量，
  双后端兼容），FK 带 `ondelete`，`proposal_rounds` 加唯一约束 `uq_proposal_rounds_proposal_round`，
  `status` / `project_id` 建索引。
- `init_db()` 启动即 `alembic upgrade head`，故本地与生产的 proposals 三表随 API 启动自动落地，
  无需手动迁移步骤。

## 兼容性
- 纯增量：不改动 `models.py` 既有导出、`api.py` 既有端点、前端任何文件。
- proposal 复用既有「项目成员作用域 + require_business_auth」鉴权范式，与 documents 一致。
