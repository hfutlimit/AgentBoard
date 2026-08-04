# Tasks — RunStatus 枚举对齐（DB 与 requirements 文档统一）

**status**: in_review

## Task 1 — 枚举统一（`domains/common/enums.py`）

- [x] `RunStatus` 增加 `CANCELLED = "cancelled"` 终态成员
- [x] `ALL_RUN_STATUSES = list(RunStatus)` 自动覆盖 5 个取值
- [x] 全代码库 `grep` 无 `queued`/`succeeded` 的 run 状态残留（仅 proposal 状态机合法使用 `queued`）

## Task 2 — CHECK 约束对齐（`domains/scheduling/models.py`）

- [x] `ck_runs_status` 扩为 `('pending','running','success','failed','cancelled')`
- [x] 与 `RunStatus` 枚举逐字一致（单一事实源，测试用正则从 SQL 抽取比对）

## Task 3 — 文档同步（`docs/requirements.md` FR-17）

- [x] 回写取值由 `queued|running|succeeded|failed|cancelled` 修正为 `pending|running|success|failed|cancelled`

## Task 4 — Alembic 迁移（`migrations/versions/k8l9m0n1o2p3_runstatus_enum_align.py`）

- [x] `down_revision = j6k7l8m9n0a1`，链尾追加，双后端兼容
- [x] 为既有库补建 `ck_runs_status` CHECK 约束（含 `cancelled`）——修复旧迁移 `a5f2e8d9b0c1` 未建约束的隐蔽缺陷
- [x] `batch_alter_table` 表重建路径（SQLite）与原生 ALTER（MariaDB）双兼容，不丢列/唯一约束
- [x] `upgrade`/`downgrade` 均实现

## Task 5 — 测试（`tests/test_epic78_story105_runstatus_align.py`，自包含）

- [x] 枚举唯一性：5 值集合，无 `queued`/`succeeded` 残留
- [x] CHECK 约束 SQL 与枚举取值集合一致
- [x] FR-17 文档含统一枚举、不含旧拼写
- [x] 空库 `upgrade head` 后 `ck_runs_status` 真实存在（含 `cancelled`）
- [x] 约束真实生效：5 合法值可写、`cancelled` 可写、`bogus` 被 DB 拒绝
- [x] 迁移不丢列 / `idempotency_key` 唯一约束保留
- [x] `scheduler.py` / `executor.py` 新枚举下可导入、语义成员有效
- [x] **7 passed**（5.04s）

## Task 6 — 回归与验收

- [x] 聚焦回归（scheduler / executor / Story 101/102 / 相关模块）无新增失败
- [x] Playwright E2E 前端核心页面冒烟 0 报错
- [x] 零 REST 契约变更；未触碰端口 18001 / docker 端口
