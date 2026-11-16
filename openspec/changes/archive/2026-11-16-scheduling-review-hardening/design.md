# Design: scheduling 审查收口

## 1. 接缝选择

沿用既有稳定接缝，不新造接口：

- `scan_stale_agent_runs(s, warn_after_seconds, takeover_after_seconds)` 的返回
  字典（`warned` / `taken_over` / `scanned_at`）是维护面合同，测试靶心落在
  `taken_over` 的成员资格上，底层 CAS 实现可替换而测试不动。
- `soft_takeover_run` 的 `Task | None` 返回语义保持不变；新增的
  `_cas_fail_stale_run(s, run, *, error_message, now) -> bool` 是两条路径共用的
  内部守卫，`error_message` 由调用方传入以保留各自既有文案
  （`stale_run_no_task` vs 追加 `\nsoft_takeover: {reason}`）。
- HTTP 层靶心是 `POST /api/worker-work/projects` 的状态码与映射副作用，
  经 `TestClient` 打真实 app，而不是直接调 service。

## 2. CAS 不变量

`UPDATE agent_runs SET status='failed', finished_at=:now, error_message=:msg
WHERE id=:id AND status='running' AND is_stale=true`，`rowcount==1` 才算回收。

where 子句是扫描器对候选的**最小**保证，因此不需要重查
`COALESCE(last_progress_at, started_at) < cutoff`；守 `is_stale` 正是关闭心跳
竞态的那一条。无 task 分支此前绕过该守卫直接改 ORM 对象，等于把「有 task
路径已修好的竞态」在相邻分支重开一遍，故收口到同一 helper 而非各写一份。

## 3. 声明端点守卫分层

```
write_actor(s, authorization, permission)   # 凭据解析 + 执行开关 → ActorContext
authorize_member(s, project_id, actor)      # 成员资格 + s.info 命令边界标志
authorize(s, project_id, authorization)     # = authorize_member(write_actor(...))
```

`authorize()` 的行为与调用方完全不变；声明端点改为 `write_actor` 一次 +
每个项目 `authorize_member`，包括 stale 映射那条容忍 403 的循环。`s.info`
标志仍按项目设置，空集声明的副作用边界与改动前一致。

审计中间件（`api.py`）本身每请求解析一次凭据，因此「每请求一次」的实测
基线是 2 次而非 1 次；回归断言改为**开销不随项目数增长**（1 项目 vs 3 项目
调用次数相等），既锁住 N+1 又不把中间件实现细节写死进测试。

## 4. 复核后撤回的两条风险（不改代码）

- **.NET presence 耦合**：`WorkerOwnedService.PresenceLoop` 把 projects POST 与
  心跳放在同一 try，看似「声明失败会停掉心跳」。但 `Validate()` 在
  `Projects.Length == 0` 时抛异常、`ExecuteAsync` 要求非空 `StartupToken`、
  preflight 还索引 `Projects[0]`，且全仓唯一运行时调用方就是它（生成的 BFF
  客户端方法无人调用）。空 scope + 无 token 的组合不可达，新守卫对 .NET
  worker 零行为变化。
- **`_caller_uid_admin` 漏校验 `revoked_at`**：`revoke_api_key` 同时置
  `enabled=False` 与 `revoked_at`，全仓只有这一处写 `revoked_at`，应用内造不出
  「enabled 且已撤销」的行；三个 `lookup_api_key_by_hash` 调用方都自查
  `enabled`。真正确认的缺陷是 `revoke_api_key` docstring 声称「lookup 已过滤
  enabled」——lookup 只按 `key_hash` 查行。改为修正该假陈述，并补 admin API
  key 回归（已撤销 → 401、有效 → 200）锁住可达不变量。

## 5. 验证

- `tests/unit/test_progress_takeover_and_linked_comments.py` 24 passed
  （新增 3 条：无 task 正常回收上报、CAS 落空不上报、扫描层不上报）。
- `tests/e2e/happy_path/test_worker_planner.py` 32 passed（新增 409 与凭据
  开销不随项目数增长；后者在 HEAD 版本实测 RED：1 项目 3 次 / 3 项目 5 次）。
- `tests/test_scan_stale_runs_auth.py` 6 passed（新增已撤销 / 有效 admin key）。
- `tests/unit/test_executor_provider_contract.py` 11 passed；临时摘掉 qoder
  实测 3 条失败，证明守卫非空。
- `WorkerStartupServiceTests` 13 passed（新增四工具逐个诊断断言，先 RED）。