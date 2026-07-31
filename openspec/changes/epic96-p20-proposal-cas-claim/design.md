# Design: Proposal 服务端 CAS 原子认领 + 显式租约

## 1. 并发认领模型

```
   Worker A ─┐                                  ┌─ 命中(queued/answered) → rowcount=1 → 200
             ├─ POST /claim ──► 单条条件 UPDATE ─┤
   Worker B ─┘       │          WHERE id=?        └─ 未命中(已 analyzing) → rowcount=0 → 409
                     │                   AND status IN ('queued','answered')
                     ▼
              DB 写锁串行化（SQLite 全局 / MariaDB 行锁）
              后到者的 UPDATE 读到已提交的 analyzing → 0 行 → 竞争失败
```

关键不变量：**判定与写入在同一条 SQL 内完成**。不存在「先读后写」的窗口，数据库引擎是唯一的
仲裁者。

## 2. 租约隔离：claimed_at 与 updated_at 解耦

```
   认领(claim_proposal)         无关写入(PATCH content / 用户作答 / PATCH converged_spec)
   ───────────────────         ──────────────────────────────────────────────────────
   claimed_at = now   ✓         claimed_at 不变 ✗（绝不续期）
   updated_at  = now   ✓         updated_at  = now  ✓（onupdate 照常刷新）

   reclaim_stale 判定：
     stale = claimed_at < cutoff                      ← 主判据
           OR (claimed_at IS NULL AND updated_at < cutoff)  ← 仅兜底迁移前遗留 NULL 行
```

这是根治「永久卡死」的核心：崩溃 Worker 的 `claimed_at` 停在其死亡时刻，无论旁人如何写入都
不会前移；租约到期后 `reclaim_stale` 必然把它捞回 `queued` 重投。

## 3. 错误语义（替代原 PUT 的含糊 200）

| 场景 | 状态码 | detail |
|---|---|---|
| 认领成功 | 200 | 返回提案（含 `claimed_by` / `claimed_at`） |
| 已被持有 / 状态不可认领（仅 `queued`/`answered` 可认领） | 409 | 含当前状态，与 400 非法迁移区分 |
| 提案不存在 | 404 | — |

`reclaim-stale` 返回 `{reclaimed:[...], count, lease_seconds}`，幂等可重复调用。

## 4. 状态机对租约的维护点

`set_proposal_status` 在写 `status` 的同时：

```python
if new is ProposalStatus.ANALYZING:
    p.claimed_at = utc_now()          # 进入 analyzing：盖租约（含旧版 PUT 认领路径）
else:
    p.claimed_by = ""                 # 离开 analyzing：清空，防脏租约残留
    p.claimed_at = None
```

旧版 `PUT /status` 认领路径（draft→queued→analyzing 的人工派发）因此也自动获得租约，
避免这类行 `claimed_at` 恒为 NULL 而崩溃后永不被回收。

## 5. busy_timeout 的意义

```python
# database.py connect 事件
cursor.execute("PRAGMA busy_timeout=10000")
```

SQLite 默认在写锁冲突时立即抛 `database is locked`。加 10s 排队后，多个 Worker 并发认领会
串行等待而非失败，既保证原子性验证不被误判，又提升高并发下认领端点的可用性。
