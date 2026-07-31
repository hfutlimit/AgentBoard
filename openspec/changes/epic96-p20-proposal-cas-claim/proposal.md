# Change: Proposal 服务端 CAS 原子认领 + 显式租约（Epic 96 · Story 156 P2-0 / Task 933）

## Why

P1-2（Task 932）交付了常驻 Worker，但其认领逻辑埋着两个**真实并发缺陷**，当时因约束
「零 REST 契约变更」只在客户端用「先 GET 复核再 PUT」规避，并把根治记入 P2 范围
（见 `epic96-p12-proposal-worker/proposal.md` 末段）。

**缺陷 1 —— 认领 TOCTOU 竞态。**
服务端 `set_proposal_status` 对同状态迁移是幂等 no-op：

```python
if current != new and new not in PROPOSAL_TRANSITIONS.get(current, set()):
    raise IllegalTransition(...)
```

即 `analyzing → analyzing` 返回 **200 而非 400**。因此「靠状态机仲裁并发认领」的假设不成立：
N 个 Worker 并发调 `PUT /status` 会**全部拿到 200**，同时认为自己抢到了提案，进而重复调用
Agent、重复回写问题、污染轮次。

客户端「先 GET 复核状态再 PUT」只能收窄窗口——读与写之间的 TOCTOU 依然存在，高并发下仍会
偶发双认领。残留风险由 `(proposal_id, round_no)` 唯一约束 + 全量重放兜底，最坏只是一次冗余
Agent 调用，但**无法从根上消灭**。

**缺陷 2 —— 租约挂靠 `updated_at` 导致永久卡死。**
崩溃恢复依赖「`analyzing` 停滞超过租约则回退 `queued`」，而判定依据是 `updated_at`。
但该列带 `onupdate=utc_now`：**用户作答、PATCH `converged_spec` 等完全与持有者无关**的写入都会
刷新它。后果是一个早已被 kill 的 Worker，其租约被旁人不断续期，提案**永久卡死在 analyzing**，
没有任何兜底能把它捞回来。

Story 156（P2 RabbitMQ）正式接入 MQ 前，必须先消灭这两个缺陷——否则多消费者 + 消息重投会
把竞态和卡死放大成生产事故。

## What Changes

新增**服务端 CAS 原子认领端点**与**显式租约字段**，把「判定」与「写入」压进单条条件 SQL，由
数据库仲裁，从根上消除竞态与卡死。

1. **`POST /api/proposals/{pid}/claim`**（新增 REST 契约）：`queued/answered → analyzing` 的
   **原子认领**。单条条件 `UPDATE ... WHERE id=? AND status IN ('queued','answered')`，
   依据 `rowcount` 仲裁——恰好一个赢家拿到 200，其余一律 **409**（已被持有 / 状态不可认领），
   不存在返回 404（提案不存在）。
2. **显式租约字段**：`Proposal` 新增 `claimed_by` / `claimed_at`（仅认领时写入，绝不随无关写入
   续期）。状态机维护它们：进入 `analyzing` 盖上 `claimed_at`；离开 `analyzing` 清空，防止已
   收敛/失败的提案仍挂着持有者。
3. **`POST /api/proposals/reclaim-stale`**（新增 REST 契约）：批量回收租约过期的 `analyzing`
   提案，判定依据** exclusively `claimed_at`**（`updated_at` 仅用于兜底迁移前遗留的 NULL 行），
   一次批量条件 UPDATE 完成，天然幂等。
4. **调用方切换**：`agentboard/worker.py` 的 `claim()` / `reclaim_stale()` 与
   `agentboard/mcp_server.py` 的 `proposal_claim` 全部改走新 CAS 端点，删除 GET+PUT 老逻辑。
5. **Alembic 迁移**：`claimed_by` / `claimed_at` 两列 + `ix_proposals_status_claimed_at` 索引，
   SQLite / MariaDB 双后端兼容。

### 为什么必须是单条条件 UPDATE，而不能「先 SELECT 再 PUT」

```sql
UPDATE proposals SET status='analyzing', claimed_by=?, claimed_at=now, error=''
 WHERE id=? AND status IN ('queued','answered')
```

- 单行条件更新的原子性由存储引擎保证：SQLite 写锁全局串行化（rollback-journal）；
  MariaDB/InnoDB 行级排他锁 + 加锁读。后到者必然读到已提交的 `analyzing` 而匹配不到任何行，
  `rowcount` 恰为 0 —— **竞争由数据库仲裁，不留任何窗口**。
- 额外加 `PRAGMA busy_timeout=10000`：并发认领从「直接报 database is locked」变为排队等待，
  避免高并发下误判失败。
- 语句顺序敏感：UPDATE 必须是本会话第一条 SQL。若先 SELECT 会先取读锁再升级写锁，并发下平白
  增加锁冲突（WAL 下还可能触发 BUSY_SNAPSHOT）。

## Impact

- **新增**：`POST /api/proposals/{pid}/claim`、`POST /api/proposals/reclaim-stale`（**契约增量，
  非破坏性变更**）；`migrations/versions/i5j6k7l8m9n0_add_proposal_claim_lease.py`。
- **改动**：`agentboard/service.py`（`claim_proposal` / `reclaim_stale_proposals` /
  `set_proposal_status` 租约维护）、`agentboard/api.py`（2 端点）、`agentboard/models.py` /
  `domains/proposals/models.py`（`claimed_by`/`claimed_at` + `CLAIMABLE_STATUSES`）、
  `agentboard/database.py`（busy_timeout）、`agentboard/worker.py`、`agentboard/mcp_server.py`
  （认领路径切换）。
- **新增测试**：`tests/test_epic96_p12_proposal_worker.py` 追加 10 项 P2-0 用例（并发原子性 +
  租约隔离 + 端点契约）。
- **未触碰**：端口 18001（WorkBuddy MCP）、任何 docker 端口映射、`frontend/` 代码。
- **P2 衔接**：接入 RabbitMQ 后，MQ 多消费者并发认领正是本端点的最大受益场景——CAS 保证恰好一个
  消费者拿到消息对应的提案，彻底消灭重复分析。
