# Epic 78 · Story 105 — RunStatus 枚举对齐（DB 与 requirements 文档统一）

**status**: in_review
**date**: 2026-08-04

## 问题

AgentRun（`agent_runs` 表）的状态取值全代码库**不止一套**，且执行器写入会被
DB 拒绝或绕过约束：

1. **代码侧 vs 文档侧不一致**：`agentboard/domains/common/enums.py` 的 `RunStatus`
   为 `pending|running|success|failed`，而 `docs/requirements.md` FR-17 写的是
   `queued|running|succeeded|failed|cancelled`。文档里的 `queued`/`succeeded`
   与代码枚举对不上——若按文档实现，执行器写 `queued` 会被 CHECK 拒绝。

2. **旧库无 CHECK 约束（更隐蔽的根因）**：旧迁移 `a5f2e8d9b0c1` 建
   `agent_runs` 表时**未创建** `ck_runs_status` CHECK 约束（约束只存在于
   `models.py __table_args__`）。凡经 Alembic `upgrade head` 构建的既有库
   （SQLite / MariaDB），`status` 列**完全无约束**——执行器可写入任意非法状态
   （如 `queued`），Story 101/102 的 executor 写的任何状态都"能过"，但这不是
   正确的校验，反而是静默的数据污染。

3. **cancelled 缺失**：`docs/requirements.md` 有 `cancelled` 终态，代码枚举没有，
   取消 AgentRun 的语义无处安放。

## 目标

1. 全代码库只有**一套** `RunStatus` 取值：`pending → running → success/failed + cancelled`
   （`cancelled` 为终态，可选语义）。
2. `domains/scheduling/models.py` 的 `ck_runs_status` CHECK 约束与枚举**逐字一致**
   （单一事实源 = 枚举）。
3. `docs/requirements.md` FR-17 与代码枚举一致（旧拼写 `queued|succeeded` 清除）。
4. 新增 Alembic 迁移 `k8l9m0n1o2p3`：为既有库**补建** `ck_runs_status` CHECK 约束
   （含 `cancelled`），使执行器写入的状态真正受 DB 校验（SQLite / MariaDB 双后端）。
5. `service.update_run` 沿用 `ALL_RUN_STATUSES`（= 枚举）校验，`cancelled` 合法。

## 非目标（后续 Change 承接）

- Story 103 Trigger（webhook 唤醒）、Story 104 Executor daemon 主循环——
  与本 Change 正交，RunStatus 对齐只是它们的前提。
- AgentSchedule 绑定松绑 → Story 106。

## 方案

### 1. 枚举（`domains/common/enums.py`）

`RunStatus` 增加 `CANCELLED = "cancelled"` 成员，最终：

```python
class RunStatus(StrEnum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    CANCELLED = "cancelled"
```

`ALL_RUN_STATUSES = list(RunStatus)` 自动覆盖 5 个值（`service.update_run` 的
合法性校验随之放行 `cancelled`）。

### 2. CHECK 约束（`domains/scheduling/models.py`）

`ck_runs_status` 由 `('pending','running','success','failed')` 扩为
`('pending','running','success','failed','cancelled')`。

### 3. 文档（`docs/requirements.md` FR-17）

回写取值由 `queued|running|succeeded|failed|cancelled` 修正为
`pending|running|success|failed|cancelled`。

### 4. 迁移（`migrations/versions/k8l9m0n1o2p3_runstatus_enum_align.py`）

- `down_revision = j6k7l8m9n0a1`（文档文件夹迁移），链尾追加。
- SQLite 不支持 `ALTER TABLE ADD CONSTRAINT` → `batch_alter_table` 触发表重建
  （Alembic 自动反射既有列，不丢数据、不丢 `idempotency_key` 唯一约束）；
  MariaDB 上 `batch_alter_table` 走原生 ALTER，同样生效。
- 双后端兼容；零 REST 契约变更；不触碰端口 18001。

### 5. 测试（`tests/test_epic78_story105_runstatus_align.py`，自包含）

1. 枚举唯一性：`RunStatus` 取值 == 5 值集合，无 `queued`/`succeeded` 残留。
2. 约束与枚举一致：解析 `ck_runs_status` SQL，取值集合 == 枚举集合。
3. FR-17 与枚举一致：文档含 `pending|running|success|failed|cancelled`，
   不含旧拼写。
4. 迁移真实落约束：临时 SQLite 空库 `upgrade head` → `inspect` 断言
   `ck_runs_status` 存在且含 `cancelled`。
5. 约束真实生效：5 个合法值可写（含 `cancelled`），非法值 `bogus` 被 DB 拒绝。
6. 迁移不丢列/唯一约束：`agent_runs` 全列 + `idempotency_key` 唯一约束完整保留。
7. 回归：`scheduler.py` / `executor.py` 在新枚举下可导入，语义成员有效。
