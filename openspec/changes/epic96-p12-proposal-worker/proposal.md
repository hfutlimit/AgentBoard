# Change: Proposal 澄清 Worker 消费者（Epic 96 · Story 155 P1-2）

## Why

Epic 96 的「Proposal 澄清回路」到目前为止交付了两块：

- **P0**（Task 922 / 930）：三张表 + 状态机 + `/api/proposals` REST + 前端问答工作台；
- **P1-1**（Task 931）：6 个 `proposal_*` MCP 工具，给无头 Agent 开了入口。

但**执行侧仍然是空的**。实测 `agentboard/` 目录下不存在任何 worker 模块，这意味着：

> 用户在 Web 端点完「派发给 Agent」，提案就**永远停在 `queued`**。

没有任何进程会去认领它。要往下走，只能靠人打开一个 MCP 客户端，手工依次调
`proposal_pending` → `proposal_claim` → `proposal_get` → `proposal_ask`。
换句话说，「人机协同需求分析」目前只完成了「人机」，没有「协同」——
自动化闭环是架构承诺，不是可用能力。

这正是 Story 155 验收范围里白纸黑字写着、却尚未交付的部分：

> 新增 Worker 进程：消费派发消息、无头拉起本机 WorkBuddy CLI、崩溃恢复；
> 会话续接采用全量重放。本轮先用 DB 轮询 `GET /api/proposals/pending`，暂不上 MQ。

## What Changes

新增 `agentboard/worker.py` —— 一个**无状态、可横向扩容、崩溃可恢复**的常驻消费者，
仅通过既有 REST 端点工作（**零 REST 契约变更**）。

1. **双源发现**：同时轮询 `queued`（首轮澄清）与 `answered`（用户答完，进入下一轮）。
   只盯 `queued` 会让澄清永远停在第二轮之前——这是本设计最容易被漏掉的一条边。
2. **认领仲裁**：`PUT /status → analyzing`，竞争失败方静默跳过。
3. **全量重放上下文**：每轮把「正文 + 全部历史问答（含 unsure 标记）」重新拼齐喂给
   Agent，Worker 自身不持有任何会话状态。
4. **无头 Agent 调用**：`AgentInvoker` 抽象 + `SubprocessAgentInvoker`
   （命令模板可配、prompt 走 stdin、从 stdout 抽取最后一个 JSON 决策对象）。
5. **决策落库**：`ask` → 回写问题推进 `awaiting`；`finalize` → 写 `converged_spec` 并
   推进 `converged`；`fail` → 落 `failed` 带原因。
6. **崩溃恢复租约**：`analyzing` 停滞超过租约（默认 30 分钟）自动回退 `queued` 重投。
7. **轮次上限护栏**：达到 `max_rounds` 仍要提问 → 转 `failed` 交人工，杜绝无限骚扰用户。
8. **CLI**：`python -m agentboard.worker --once | --loop`，配置全走环境变量。

### 实现期发现的一个真实缺陷（已在 Worker 侧规避）

服务端 `set_proposal_status` 对**同状态迁移是幂等 no-op**：

```python
if current != new and new not in PROPOSAL_TRANSITIONS.get(current, set()):
    raise IllegalTransition(...)
```

即 `analyzing → analyzing` 返回 **200 而非 400**。原本设计里「靠状态机仲裁并发认领」
的假设因此**不成立**——两个 Worker 都会拿到 200，同时认为自己抢到了提案，进而重复
调用 Agent。

由于本任务约束「零 REST 契约变更」，修复放在客户端：`claim()` 先 `GET` 复核当前状态，
只有仍处于 `queued`/`answered` 才发起迁移。残留的 TOCTOU 窗口由
`(proposal_id, round_no)` 唯一约束 + 全量重放的幂等性兜底，最坏结果只是一次冗余的
Agent 调用，不会污染数据。**彻底消灭需要服务端提供 CAS 语义的认领端点，已记入 P2 范围。**

## Impact

- **新增**：`agentboard/worker.py`、`tests/test_epic96_p12_proposal_worker.py`（27 项）、
  `tests/test_epic96_p12_proposal_worker_e2e.py`（1 项 Playwright）。
- **未改动**：`api.py` / `service.py` / `models.py` / 前端 —— 零契约变更、零回归面。
- **未触碰**：端口 18001（WorkBuddy MCP 通信占用）、任何 docker 配置。
- **P2 衔接**：接入 RabbitMQ 时只需替换 `fetch_work()` 的数据来源，
  `claim → 重放 → 调用 → 落决策` 流水线原样保留。
