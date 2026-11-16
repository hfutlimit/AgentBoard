# Proposal: scheduling 审查收口（声明守卫 + 回收上报不变量）

## Why

对 `656eaf1..3136720` 做双轴审查时，发现两处已实现行为没有被任何规格覆盖，
以及一处只修了探针路径、未收口到不变量的竞态：

1. `POST /api/worker-work/projects` 在 `3136720` 加了无条件凭据校验与执行开关
   门，但 capability spec 的「Worker 项目范围声明按成员资格收敛」只描述成员
   资格与空集语义，全局 auth 条款又只在 `AGENTBOARD_REQUIRE_AUTH=1` 时要求
   token——规格与实现不一致。同一端点还手抄了 `authorize()` 的前两步并丢弃
   `ActorContext`，导致每个项目重复解析凭据（3 项目声明实测 5 次）。
2. `484d02d` 把 CAS 落空的 run 从 `taken_over` 剔除，但相邻的 `task_id is None`
   分支仍直接 ORM 改 `status='failed'`：同一心跳竞态下，schedule-level run 的
   恢复会被覆盖，且未发生的回收被计入 `taken_over`。该行为在 openspec 中
   grep `taken_over` / `soft_takeover` / `stale` 零命中，无规格可依。

## What Changes

- 声明端点：抽出 `write_actor`（凭据 + 执行开关）与 `authorize_member`（成员
  资格 + 命令边界标志），`authorize()` 语义不变，声明端点每请求只解析一次
  凭据；docstring 补 401/409 前置契约。
- 回收路径：抽出 `_cas_fail_stale_run`，`soft_takeover_run` 与无 task 分支共用
  同一 CAS，`rowcount` 为唯一事实来源，落空即不上报。
- 规格：MODIFIED 声明 requirement 补前置门与单次解析；ADDED「Stale run 回收
  上报与实际回收一致」requirement，覆盖两条路径与三种场景。

## Non-goals

- 不改 worker-owned 平面的鉴权强度（该平面本来就无条件要求凭据，与软鉴权的
  `workers/register` 不同源），只把既有行为写进规格并去掉重复解析。
- 不改 `taken_over` 的字段形状或维护 API 合同。
- 不修既有 flaky 测试（`test_worker_owned_work.py` 整文件非确定性，已取证与
  本轮无关，记录在 `qoder-worker-provider/tasks.md` 附录）。
- 不做 provider 注册表集中化重构。

## Impact

| 面 | 影响 |
|---|---|
| REST 合同 | 无形状变化；401/409 前置条件由既有实现固化进规格 |
| 性能 | 声明端点凭据解析从 O(项目数) 降为每请求一次 |
| 调度语义 | 心跳恢复的 schedule-level run 不再被误标 failed / 误上报 |
| 规格 | `openspec/specs/agentboard/spec.md` 同步一处 MODIFIED、一处 ADDED |