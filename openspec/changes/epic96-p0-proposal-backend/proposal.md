# 变更提案：Proposal 澄清回路 · P0 后端基座（Epic 96 P0）

## 背景
Epic 96（Proposal 澄清回路：人机协同需求分析）目标是让 Web 端创建 Proposal（需求提案），
服务端经 MQ 派发至工作者机器，无头拉起本机 WorkBuddy 进行多轮需求澄清，收敛后由人工终审
生成 Story。Epic 按 P0–P3 分期交付，本变更完成 **P0 的后端基座**：

- 建立 `Proposal` 领域实体与状态机；
- 提供 CRUD + 轮次问答 REST API，作为后续 P1（MCP 4 工具 + Worker）与前端问答工作台的契约；
- 先以手工触发代替 MQ，把后端人机交互闭环的数据结构跑通。

当前代码缺少独立的 Proposal 存储，需求提案如果临时塞进 `Task.spec` 会污染既有任务状态机与
搜索/统计契约，因此**不复用 Task，新增三张独立表**。

## 目标
交付一组纯增量、双后端（SQLite / MariaDB）兼容的后端能力：
1. `proposal` / `proposal_round` / `proposal_question` 三张表；
2. `Proposal` 状态机（draft→queued→analyzing→awaiting→answered→converged→story_created / failed）；
3. CRUD + 轮次问答 REST API；
4. at-least-once 幂等兜底（同一 proposal + round 唯一约束防重投）。

## 非目标
- 不实现 MQ 派发 / Worker 消费者（P1）；
- 不实现前端问答工作台 UI（同属 P0 的故事 #154 前端部分，本变更只提供契约与后端）；
- 不实现 proposal→story 的自动生成（P3）；
- 不改动任何既有端点契约、模型或前端文件。

## 范围
- `agentboard/domains/proposals/models.py`：`Proposal` / `ProposalRound` / `ProposalQuestion`
  实体 + `ProposalStatus` 枚举 + `PROPOSAL_TRANSITIONS` 状态机表。
- `agentboard/models.py` facade 导出上述符号。
- Alembic 迁移 `migrations/versions/h4i5j6k7l8m9_add_proposals.py`：新增三表（含状态 CHECK 约束、
  FK、索引、唯一约束），双后端兼容。
- `agentboard/service.py`：proposal 服务层（create / get / list / update / delete / set_status /
  round / questions / answer + 自动推进 + 幂等）。
- `agentboard/api.py`：REST 端点 `/api/proposals`（CRUD + status + rounds + questions + answer）。
- `tests/test_epic96_p0_proposals.py`：17 个 pytest 覆盖模型、状态机、REST 全链路。
- `tests/test_epic96_p0_proposals_e2e.py`：Playwright 真实浏览器冒烟（登录 + 列表/看板 + 新端点）。

## 影响
- 仅新增文件与纯增量改动；不触碰既有 models / api 契约。
- 新增端点（均需 `api:read` 读、`api:write` 写；本地开放模式公开）：
  - `POST /api/proposals`、`GET /api/proposals`、`GET /api/proposals/pending`（Worker 轮询）
  - `GET /api/proposals/{pid}`、`PATCH /api/proposals/{pid}`、`PUT /api/proposals/{pid}/status`
  - `DELETE /api/proposals/{pid}`、`POST /api/proposals/{pid}/questions`
  - `GET /api/proposals/{pid}/rounds`、`PUT /api/proposal-questions/{qid}/answer`
- 访问控制在 `require_business_auth` 中间件层接入，proposal 按所属项目成员作用域过滤
  （与管理文档 documents 一致）。

## 退出标准
- 状态机非法迁移返回 400；合法链路可从 draft 走到 story_created（或 failed→重入）。
- 提问需 proposal 处于 analyzing；作答后自动推进 awaiting→answered。
- 同一 proposal + round 唯一约束使重复 MQ 重投复用既有 round（at-least-once 兜底）。
- pytest 覆盖模型、状态机、REST 全链路且不破坏既有契约（纯增量）。
- Playwright 冒烟确认前端无回归，新增端点经真实运行后端验证可用。
