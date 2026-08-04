# Design — RunStatus 枚举对齐（DB 与 requirements 文档统一）

## 架构定位

```
                    ┌────────────────────────────────────────────┐
                    │  RunStatus（唯一事实源）                    │
                    │  pending → running → success/failed         │
                    │                              + cancelled    │
                    └──────┬──────────────────┬───────────────────┘
                           │                  │
          ┌────────────────▼───────┐   ┌──────▼──────────────────────┐
          │ models.py ck_runs_status│   │ service.update_run          │
          │ CHECK 约束（逐字一致）  │   │ ALL_RUN_STATUSES 校验       │
          └─────────────────────────┘   └─────────────────────────────┘
                           │                  │
          ┌────────────────▼───────┐   ┌──────▼──────────────────────┐
          │ Alembic k8l9m0n1o2p3   │   │ docs/requirements.md FR-17  │
          │ 为既有库补建 CHECK     │   │ 回写取值与枚举一致           │
          └─────────────────────────┘   └─────────────────────────────┘
```

## 关键设计决策

### 1. 枚举是唯一事实源，CHECK 约束从枚举生成语义

不手工维护两份取值清单。`RunStatus` 枚举（`common/enums.py`）定义 5 个合法值；
`models.py` CHECK 约束与枚举逐字一致；`ALL_RUN_STATUSES = list(RunStatus)`
驱动 `service.update_run` 的运行时校验。三处共用一个概念集合，杜绝再次漂移。

### 2. 为什么选 `pending|running|success|failed|cancelled` 而非文档旧值

- `queued`/`succeeded` 是旧文档拼写，与 `scheduler.py`/`executor.py` 实际写入
  （`RunStatus.PENDING`/`SUCCESS`）不一致——若按文档改代码，等于要改执行器全部
  写入点，且 `succeeded` 是英文分词错误（正确是 `success`）。
- `pending` 语义更准确：AgentRun 创建即 `pending`（等待执行器认领），不是入队
  `queued`（执行器没有队列）。
- `cancelled` 保留：文档既有终态语义，人工/策略取消 AgentRun 时有合法落点。

### 3. 迁移为何必须用 `batch_alter_table`

SQLite 不支持 `ALTER TABLE ... ADD CONSTRAINT`（CHECK 只能建表时内联或表重建）。
Alembic 的 `batch_alter_table` 走「反射 → 建新表 → 拷贝数据 → 换名」路径，
在 SQLite 与 MariaDB 上都可用：SQLite 触发表重建，MariaDB 走原生 ALTER。
`copy_from=None` 让 Alembic 自动反射既有列，不丢数据、不丢 FK/唯一约束
（`idempotency_key` 唯一索引在 `__table_args__`，反射后重建保留）。

### 4. 为什么加"迁移真实落约束"测试

既有库对 `status` 列无约束是本次修复的**隐蔽根因**——`upgrade head` 后约束
必须真实存在于 DB（而非仅存在于模型定义）。测试用临时 SQLite 空库跑完整迁移链
（`init_db()` → `upgrade head`），`inspect.get_check_constraints` 断言约束存在，
再实际写入 `cancelled`（可写）与 `bogus`（被拒），从 DB 层面自证。

### 5. 零契约变更

`api.py` 的 RunUpdate/`update_run` 端点行为不变；仅新增合法取值 `cancelled`。
迁移只补约束，不动数据。不触碰端口 18001（纯本地文件 + 临时 SQLite 测试）。
